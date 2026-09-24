# Patient registration voice agent

A phone-based AI intake coordinator that registers new U.S. patients through natural conversation, saves them to Postgres, and exposes the records through a REST API and a small dashboard. Call the number, talk to "Ava," and the record shows up on the dashboard a few seconds after you confirm your details. Call back from the same phone and she recognizes you.

## Try it

| | |
|---|---|
| Phone number | **+1 (757) 979-8389** |
| Dashboard | https://patient-voice-ai-agent.vercel.app/ |
| API base URL | https://patient-voice-ai-agent.vercel.app |
| Interactive API docs | https://patient-voice-ai-agent.vercel.app/docs |
| Health check | https://patient-voice-ai-agent.vercel.app/health |
| Demo video | https://youtu.be/ORwM9DsFnMA |

No credentials are needed to test. Please use made-up patient data.

Some things worth trying on a call:

- Give a birth date in the future, or a phone number with too few digits. Ava says what's wrong and asks for that one field again.
- During the read-back, correct a spelling: "Actually, it's D-A-V-I-S, not D-A-V-I-E-S."
- Answer several questions at once ("I'm Jane Smith, born July 4th 1988, female").
- Say "can we start over?" halfway through.
- Say "Hablo español." The rest of the call continues in Spanish.
- Give the phone number of the seed patient Jane Doe, 512-555-0142. Ava says a record already exists and asks for the date of birth (03/15/1985) before offering to update it.
- Call a second time from the same phone after registering. Ava recognizes you from caller ID.
- After registering, accept the offer to book a first appointment.

## Architecture

```
 Caller (phone / web)
        |
        v
 +-------------------------------+
 | Vapi                          |   telephony, speech-to-text,
 | GPT-4.1 + system prompt       |   LLM turn-taking, text-to-speech
 +-------------------------------+
        |  tool calls + end-of-call report (HTTPS, x-vapi-secret header)
        v
 +----------------------------------------------------------+
 | FastAPI on Vercel (serverless, region cle1)              |
 |                                                          |
 |  routes/vapi.py      Vapi wire format -> tool calls      |
 |  agent_tools.py      5 voice tools, spoken-friendly      |
 |                      results, never silent on failure    |
 |  routes/patients.py  REST API, {data, error} envelope    |
 |  routes/pages.py     dashboard, /calls, /health          |
 |           \                 /                            |
 |            service.py   (the only code that touches DB)  |
 |            schemas.py   (validation + normalization)     |
 |            models.py    (tables + CHECK constraints)     |
 +----------------------------------------------------------+
        |
        v
 Neon Postgres (us-east-2)   patients, call_logs, appointments
```

The voice tools and the REST API both go through the same service layer and the same Pydantic schemas. There is exactly one place where a patient gets validated and written, so the agent can't save anything the API would reject, and vice versa.

### What happens during a call

1. Vapi answers and Ava greets the caller. In the background she calls `lookup_patient` with the caller ID.
2. If a record exists, she asks whether the caller wants to update it, and verifies the date of birth before revealing or changing anything.
3. Otherwise she collects the required fields one at a time, validating each answer as she hears it, then offers the optional fields using the wording from the spec.
4. She reads everything back in short chunks (names spelled out, phone numbers in 3-3-4 groups) and applies any corrections.
5. After an explicit "yes," she calls `register_patient`. The backend validates again and saves. The result has a `status` field that tells her what to say: `saved`, `validation_error` (with the exact field names), or `system_error`.
6. "You're all set, [First Name]." She offers a first appointment, then ends the call.
7. Vapi sends an end-of-call report; the backend stores the transcript and summary, linked to the patient.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Voice platform | Vapi | Handles telephony, STT, TTS, interruptions and turn-taking, so the three hours go into the conversation design and the backend. It also provides a free U.S. number, which mattered since I'm based in Pakistan and couldn't easily verify a Twilio account. |
| LLM | OpenAI GPT-4.1 | Of Vapi's presets it had the best balance of latency (about 1.5 s per turn) and reliable tool calling. Claude Sonnet 4.6 scored higher on reasoning but added roughly 0.6 s per turn, which callers notice. |
| Backend | Python, FastAPI | Pydantic gives field-level validation errors for free, and those errors map directly into messages the agent can speak. |
| Database | PostgreSQL on Neon | Real constraints (CHECK, types) instead of SQLite's loose typing, persistent across redeploys, free tier, no card needed. |
| Hosting | Vercel (Python serverless) | Free, deploys on every push, no sleep-on-idle like Render's free tier (a sleeping server would time out the first tool call of a call). The function runs in Cleveland, next to the Ohio database. |
| ORM | SQLAlchemy 2.0 | Typed models, and the same models create the SQLite test database. |
| Tests | pytest + FastAPI TestClient | 21 tests against an in-memory SQLite database, no network needed. |

## Data model

`patients` follows the spec's field list. Every rule is enforced twice: Pydantic in `app/schemas.py` gives the friendly error, and a Postgres CHECK constraint in `app/models.py` guarantees nothing invalid lands in the table even if someone bypasses the API.

| Field | Stored as | Rule |
|---|---|---|
| patient_id | UUID, primary key | generated |
| first_name, last_name | varchar(50), not null | letters, spaces, hyphens, apostrophes |
| date_of_birth | date, not null | real date, not in the future, after 1900; API returns MM/DD/YYYY |
| sex | varchar, not null | Male, Female, Other, Decline to Answer |
| phone_number | char(10), not null | valid U.S. number (NANP: area code and exchange can't start with 0 or 1) |
| email | varchar(254) | valid email, lowercased |
| address_line_1 / _2 | varchar(200) | line 1 required |
| city | varchar(100), not null | 1 to 100 characters |
| state | char(2), not null | USPS code (50 states, DC, territories) |
| zip_code | varchar(10), not null | 12345 or 12345-6789 |
| insurance_provider, insurance_member_id | varchar | member ID alphanumeric |
| preferred_language | varchar, not null | defaults to English |
| emergency_contact_name / _phone | varchar | phone validated like the patient's |
| created_at, updated_at, deleted_at | timestamptz (UTC) | deleted_at set by soft delete |

Validators also normalize what speech-to-text produces: "(512) 555-0142", "+1 512 555 0142" and 5125550142 all become `5125550142`; "texas" becomes `TX`; "prefer not to say" becomes `Decline to Answer`.

Two extra tables support the bonus features: `call_logs` (Vapi call ID, transcript, summary, duration, end reason, linked patient) and `appointments` (mock single-provider schedule, one booking per slot).

Indexes cover the three list filters (`phone_number`, `last_name`, `date_of_birth`).

## REST API

All responses use `{"data": ..., "error": ...}`. Errors look like:

```json
{
  "data": null,
  "error": {
    "code": "validation_error",
    "message": "One or more fields are invalid",
    "details": [{ "field": "date_of_birth", "message": "cannot be in the future" }]
  }
}
```

| Method | Path | Notes |
|---|---|---|
| GET | `/patients` | Filters: `last_name` (case-insensitive), `date_of_birth` (MM/DD/YYYY or YYYY-MM-DD), `phone_number` (any format). Also `limit`, `offset`. |
| GET | `/patients/{id}` | 400 for a malformed UUID, 404 if missing or deleted |
| POST | `/patients` | 201 with the created record; unknown fields are rejected |
| PUT | `/patients/{id}` | Partial update; required fields can't be set to null; empty body is a 400 |
| DELETE | `/patients/{id}` | Soft delete (sets `deleted_at`); the record disappears from all reads |
| GET | `/patients/{id}/calls` | Transcripts and summaries for that patient |
| GET | `/patients/{id}/appointments` | Booked appointments |
| GET | `/calls` | Recent calls, including ones that ended without a registration |
| GET | `/health` | Checks the database connection |
| POST | `/vapi/webhook` | Vapi only; requires the `x-vapi-secret` header |

Status codes: 200, 201, 400 (malformed JSON, bad UUID, empty update), 401 (webhook secret), 404, 422 (validation), 500 (unexpected or database error, with no internals leaked).

```bash
BASE=https://patient-voice-ai-agent.vercel.app

curl "$BASE/patients?last_name=doe"

curl -X POST "$BASE/patients" -H "Content-Type: application/json" -d '{
  "first_name": "Maria", "last_name": "Lopez", "date_of_birth": "07/04/1988",
  "sex": "Female", "phone_number": "512-555-0166", "address_line_1": "1 Main St",
  "city": "Austin", "state": "TX", "zip_code": "78701"
}'

curl -X PUT "$BASE/patients/<patient_id>" -H "Content-Type: application/json" \
  -d '{"email": "maria@example.com"}'

curl -X DELETE "$BASE/patients/<patient_id>"
```

## Prompt engineering

The full system prompt is in [`prompts/system_prompt.md`](prompts/system_prompt.md), with a table explaining the reason behind each rule. The main decisions:

- One question per turn, one or two sentences. Long turns are what makes voice agents sound robotic.
- Accept answers in any order and never re-ask something already given.
- Validate each answer as it's heard, so the agent re-asks for one field instead of failing at save time. The backend still validates everything and is the source of truth.
- Spell names back letter by letter, read phone numbers in groups, say dates with month names. Ask about things that are easy to mishear (fifteen/fifty, B/D).
- Read-back and an explicit yes before any write.
- Every tool result carries a `status` and often an `instruction`, so the model always knows what to say. A failed save is always spoken ("your information was not saved"), never silence and never a fake success. A failed lookup at the start of the call is skipped quietly, since the call can still succeed.
- Returning callers are found by caller ID or by the number they say, but nothing stored is read out until the date of birth matches.

Tool definitions are in [`vapi/tools/`](vapi/tools) and every Vapi setting (model, turn-taking, timeouts, why each value was chosen) is recorded in [`vapi/assistant.md`](vapi/assistant.md).

## Edge cases

| Situation | What happens |
|---|---|
| Invalid date of birth (future, impossible date) | Agent says why and re-asks that field; backend rejects it too (422 via API) |
| Wrong number of phone digits | Agent says how many digits it heard and re-asks |
| Correction mid-call | Agent updates the field and re-reads only that field |
| Caller wants to start over | Agent confirms, clears everything, restarts from the name |
| Caller interrupts | Agent stops talking (triggers after 2 words, so a stray "mm-hmm" doesn't) |
| Database write fails | Tool returns `system_error`; agent apologizes, says nothing was saved, suggests calling back. Tested by breaking `DATABASE_URL` on a live deployment. |
| Backend unreachable | Vapi's "request failed" message is spoken instead of silence |
| Call drops mid-conversation | Nothing is saved (only confirmed records are written). The call still appears on the dashboard's Calls tab as "Not registered" with its transcript. |
| Caller refuses a required field | Agent explains it's required; if still refused, ends politely without saving |
| Same phone, several family members | Lookup returns all matches; agent asks which person is calling |
| Medical emergency mentioned | Agent tells the caller to hang up and call 911; no medical advice |
| Duplicate tool call / slot already taken | Booking re-checks availability and a unique constraint blocks double booking |

## Bonus features

- Duplicate detection: by caller ID and by spoken phone number, with date-of-birth verification before updating.
- Appointment scheduling: next three business days, four mock slots per day, booked from the call.
- Multi-language: switches to Spanish on request; the transcriber runs in multilingual mode.
- Call transcripts: stored per call and linked to the patient, visible in the dashboard.
- Dashboard: patient list with search, detail panel with appointments and transcripts, recent calls tab. Auto-refreshes every 20 s.
- Automated tests: 21 API and webhook tests.

## Run it locally

Requires Python 3.11+ and a Postgres database (a free Neon project works).

```bash
git clone https://github.com/SMH125194055/PatientVoiceAiAgent.git
cd PatientVoiceAiAgent
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env               # then fill in DATABASE_URL and VAPI_WEBHOOK_SECRET
python -m app.init_db              # creates tables and two fake seed patients
uvicorn app.main:app --reload      # http://localhost:8000
python -m pytest -q                # uses in-memory SQLite, doesn't touch your database
```

To test voice locally, expose the server with ngrok and point the Vapi tools and assistant server URL at `https://<ngrok-id>.ngrok.app/vapi/webhook`.

### Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Postgres connection string. Neon's `postgresql://` form works; the app switches it to the psycopg 3 driver. |
| `VAPI_WEBHOOK_SECRET` | yes | Shared secret Vapi sends in `x-vapi-secret`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `INTAKE_PHONE_NUMBER` | no | Number shown on the dashboard (default +17579798389) |
| `CLINIC_TIMEZONE` | no | Timezone for mock appointment slots (default America/New_York) |
| `VAPI_API_KEY`, `PUBLIC_BASE_URL` | no | Local reference only; the deployed app doesn't use them |

### Deploying

The repo deploys to Vercel as is: framework preset "Other," then set `DATABASE_URL`, `VAPI_WEBHOOK_SECRET` and `INTAKE_PHONE_NUMBER`. Run `python -m app.init_db` once against the production database.

`vercel.json` rewrites every path to `api/index.py` and passes the original path as `__path`. A small middleware (`app/vercel_paths.py`) restores it before routing. I added this after the first deploy returned 404 for every route: Vercel detected FastAPI and served the app directly, but with the rewritten path. The middleware makes routing work whichever entrypoint Vercel uses, and a test simulates the rewrite.

## Project structure

```
app/
  main.py            app setup, middleware, routers, logging
  config.py          environment variables
  db.py              engine (NullPool for serverless) and sessions
  models.py          tables and CHECK constraints
  schemas.py         validation, normalization, error formatting
  service.py         all database reads and writes
  agent_tools.py     the five voice tools
  errors.py          envelope and exception handlers
  vercel_paths.py    restores paths behind the Vercel rewrite
  init_db.py         create tables + seed data
  routes/            patients.py, vapi.py, pages.py
  static/            dashboard.html
api/index.py         Vercel entrypoint
prompts/             system prompt with design notes
vapi/                assistant settings and tool definitions
tests/               pytest suite
```

## Security and observability

- No secrets in the repo; `.env` is git-ignored and `.env.example` has empty placeholders.
- The Vapi webhook checks `x-vapi-secret` with a constant-time comparison and returns 401 otherwise.
- Input is sanitized (control characters stripped, whitespace collapsed), queries are parameterized through SQLAlchemy, unknown fields are rejected, and the dashboard renders data with `textContent` only, so stored text can't inject HTML.
- Logs go to stdout (Vercel Logs). Every registration logs the final payload (`patient_registered ... payload={...}`), every tool call logs its name and result status, and every call end logs the reason, duration and summary.

## Known limitations and trade-offs

- The REST API and dashboard have no authentication, so reviewers can use them without credentials. A real deployment would put them behind auth.
- Not HIPAA compliant, as the brief allows: data isn't encrypted at the field level, and Vapi's HIPAA mode is a paid add-on. Only fake data should be used.
- The phone line runs on Vapi trial credits (roughly 45 minutes of talk time). If the number stops answering, the credits have run out.
- The free Vapi number is inbound only, which is all this use case needs.
- Callers using VoIP apps may present a shared or random caller ID, so recognition by caller ID can miss; saying the phone number during the call still finds the record.
- Names allow spaces and non-English letters in addition to the spec's letters, hyphens and apostrophes, so "Mary Ann" and "José" are accepted.
- The first request after a period of inactivity can take a second or two longer (Vercel cold start plus Neon waking up). Tool timeouts are 20 s, so this doesn't break calls.
- Tables are created with SQLAlchemy's `create_all`; there are no migrations yet.
- Updates made by voice can change fields but can't clear an optional field.
- Appointments are mock data for a single provider.

## Next steps

- Alembic migrations, and API keys or OAuth on the REST API and dashboard.
- Rate limiting on the public endpoints.
- Automated conversation tests with Vapi's simulations, covering the edge-case table above on every prompt change.
- Field-level encryption for PHI and a HIPAA-eligible setup (BAAs with Vapi, the LLM provider and the database host).
- Send an SMS confirmation with the appointment time.
- For lower cost and more control over latency at scale, move from Vapi to a self-hosted pipeline (Twilio media streams with Pipecat or LiveKit Agents). The tool layer and service layer would stay the same; only `routes/vapi.py` would be replaced.

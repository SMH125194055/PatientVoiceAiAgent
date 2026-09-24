# Vapi assistant configuration

The assistant lives in the Vapi dashboard. This file records its settings so it
can be rebuilt exactly. Tool definitions are in `vapi/tools/*.json` and the
system prompt is in `prompts/system_prompt.md`.

## Conversation

| Setting | Value | Why |
|---|---|---|
| Name | Patient Intake Agent | |
| First message | "Thanks for calling Bayview Medical Group, this is Ava. I can help you register as a new patient or update your information. Could I start with your first and last name?" | Greets, states the purpose and asks the first question in one breath, so there's no "how can I help you" loop. |
| First message mode | Assistant speaks first | Callers expect a greeting. |
| Model | OpenAI GPT-4.1, temperature 0.3, max tokens 500 | Picked from Vapi's presets as the best mix of speed (about 1.5 s end to end) and reliable tool calling. Claude Sonnet 4.6 scored higher on intelligence but added about 0.6 s per turn, which is noticeable on a phone call. Low temperature keeps field formats consistent; 500 tokens leaves room for a full `register_patient` call. |
| Prompt caching | 24 hours, key `patient-intake-v1` | The system prompt is long; caching trims latency and cost per turn. |
| Transcriber | Multilingual mode | English and Spanish are both understood without restarting the call. |
| Voice | Natural voice matching the "Ava" persona | |
| Tools | lookup_patient, register_patient, update_patient, get_available_slots, book_appointment, plus Vapi's End Call function | |

## Turn-taking

| Setting | Value | Why |
|---|---|---|
| Start speaking wait | 0.4 s | Default. |
| Wait after a number | 1.0 s (default 0.5) | People pause between groups of digits in phone numbers and birth dates; the agent shouldn't jump in mid-number. |
| Smart endpointing | Off | The LiveKit endpointing model is English-only and would hurt the Spanish flow. |
| Stop speaking after | 2 words, 0.2 s of voice | A lone "mm-hmm" during the read-back doesn't cut the agent off, but "no, wait" does. |
| Back-off after interruption | 1 s | Default. |

## Call handling

| Setting | Value | Why |
|---|---|---|
| Silence timeout | 30 s | Ends abandoned calls instead of burning minutes. |
| Max duration | 600 s | A registration takes 3 to 5 minutes. |
| Idle message | "Are you still there?" after 15 s, at most twice | 15 s gives people time to find their insurance card. |
| Keypad input | On, `#` delimiter, 2 s timeout | Callers can type a phone number or ZIP if speech recognition struggles. |
| End call message | "Thanks for calling Bayview Medical Group. Goodbye." | |
| End call phrases | `goodbye,have a good day` | "take care" was removed: phrases match as substrings, so "I'll take care of that" would have hung up mid-call. |
| Voicemail detection | Off | Inbound only. |
| Forwarding number | None | There is no human line to transfer to; failures are handled by the agent speaking an apology. |
| Recording, logging, transcript | On | |

## Server

| Setting | Value |
|---|---|
| Server URL | `https://patient-voice-ai-agent.vercel.app/vapi/webhook` |
| Auth | `x-vapi-secret` header (value only in Vapi and Vercel, never in git) |
| Timeout | 20 s |
| Server messages | `end-of-call-report` only. Tools have their own server URL, and other event types would only add requests the backend ignores. |

## Phone number

+1 (757) 979-8389, a free Vapi number (inbound only), assigned to this assistant.

# Vapi assistant configuration

The assistant is configured in the Vapi dashboard. This file records every
setting so the agent can be recreated exactly. Tool definitions are in
`vapi/tools/*.json`; the system prompt is in `prompts/system_prompt.md`.

| Setting | Value | Why |
|---|---|---|
| Name | Patient Intake Agent | |
| First message | "Thanks for calling Bayview Medical Group, this is Ava. I can help you register as a new patient or update your information. Could I start with your first and last name?" | Greets, states purpose, and asks the first question in one breath so there is no awkward "how can I help" loop. |
| First message mode | Assistant speaks first | Callers expect a greeting. |
| Model | OpenAI GPT-4.1 (temperature 0.3) | Reliable tool calling and instruction following with low latency; low temperature keeps field formatting consistent. |
| Transcriber | Deepgram Nova-3, language `multi` | Multilingual mode so English and Spanish are both understood without a restart. |
| Voice | A warm female voice on a multilingual model (ElevenLabs Flash v2.5 or Cartesia Sonic) | Must be able to speak Spanish for the language-switch bonus. |
| Tools | lookup_patient, register_patient, update_patient, get_available_slots, book_appointment, plus Vapi's built-in End Call | |
| Server URL | `https://patient-voice-ai-agent.vercel.app/vapi/webhook` with header `x-vapi-secret` | Receives the end-of-call report (transcript and summary). |
| Server messages | end-of-call-report (plus defaults) | Transcripts are stored and linked to the patient. |
| Summary | Enabled (analysis) | Shown on the dashboard per call. |
| Silence timeout | 30 seconds | Ends abandoned calls instead of burning minutes. |
| Max call duration | 600 seconds | Registration takes 3 to 5 minutes; this caps runaway calls. |
| Background denoising | On | Phone calls often come from noisy places. |
| Phone number | +1 (757) 979-8389 (free Vapi number, inbound only) | Assigned to this assistant. |

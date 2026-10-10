# DealBrain

Live sales copilot: listens to a call, works out who is speaking, stays quiet through greetings and small talk,
and when the customer asks for something or raises a concern it checks the Company Brain (docs + deterministic
rules) and shows the rep an approved answer and a line to say in well under a second. Accepted terms update the
CRM and contract; the call ends with a post-call summary.

## Run locally

```bash
cp .env.example .env            # keys optional: no keys = rules-only text mode
# backend
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/ on mac/linux)
.venv/Scripts/python ../tools/get_voice_model.py   # 26.5 MB speaker-recognition model (one time)
.venv/Scripts/python -m uvicorn main:app --reload --port 8000
# frontend (second terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173
```

Single-port mode (also what Replit runs): `bash start.sh` → http://localhost:8000

## Demo pages

- `/` : the rep's copilot. Pick **This mic only** (one mic hears both people; speakers identified by voice) or
  **Meet tab + mic** (tab = customer, mic = rep), then **Start listening** once. The browser asks for access a single
  time; Pause/Resume, End call and New call reuse the same streams. In mic-only mode, click **Calibrate my voice**
  and say one sentence for the most reliable speaker labels.
- `/customer` : soundboard for a second laptop. Plays scripted customer lines as ElevenLabs TTS, or sends them as text.

## How a turn flows

| Step | File | What happens |
|---|---|---|
| STT | `backend/stt.py` | ElevenLabs Scribe v2 realtime. If the upstream socket drops *or silently stalls* (speech sent, no reply for 2.5s), it reconnects and replays the audio since the last finished sentence, so nothing is lost. Batch STT fallback |
| Who spoke | `backend/voice.py`, `backend/agent/speaker.py` | **Meet mode:** the audio source. **Single mic:** each utterance's voiced audio → 256-d voiceprint (WeSpeaker ResNet34 via sherpa-onnx, local CPU, no API call) → online speaker clustering (with merging when one person's first clips split). Each voice's role is learned from calibration, your label corrections, the LLM's speaker calls and strong wording cues (e.g. reading the card aloud), decided *jointly* across voices. Once known, the voice alone labels every turn, small talk included |
| Gate | `backend/agent/gate.py` | Free, local: does this customer turn need an answer? Yes for recognised asks, or a question/request/concern about a topic the Company Brain covers (IDF-weighted overlap with its vocabulary). Greetings, rapport, travel, sports, "sounds good" → **no API call at all** |
| Understand | `backend/agent/trigger.py` | Instant extractor for ~30 situations (discounts, payment terms, pilots, renewal caps, start dates, fee waivers, seats, HIPAA, security, integrations, objections, procurement…); `FAST_MODEL` reads the whole sentence in context to confirm/revise, catch follow-ups and paraphrases, and add asks the keywords missed |
| Retrieve | `backend/agent/retrieve.py`, `brain/ingest.py` | Hybrid, sentence-level: embedding of the whole utterance fused with BM25, rolled up to the paragraph; cards quote the 2 best sentences |
| Decide | `backend/agent/rules.py` + `brain/rules.json` | **Authoritative** for negotiable asks (approved / counter / escalate + approver). Knowledge questions and objections are answered from the docs |
| Respond | `backend/agent/respond.py` | `SMART_MODEL` streams `ANSWER:` / `SAY:` grounded in the verdict and sources |
| CRM / contract / summary | `crm.py`, `contract.py`, `summary.py` | SQLite + clause-level diff + post-call summary |

The header shows live API usage (LLM calls, embedding calls, turns that cost nothing) and how many voices are known.

## Company Brain

25 docs in `backend/brain/docs/` covering the whole call, not just pricing: tiers, discounts, payment terms,
pilots, implementation/onboarding, renewals and contract terms, seats, integrations, security, data ownership,
compliance (HIPAA, SOC 2, GDPR, residency), liability, SLA/support, AI features, roadmap, references,
procurement, company overview, and objection talk tracks (price, competitor, timing/budget, stakeholders).
`rules.json` holds the negotiable limits; docs and rules agree.

## Evaluation

| Command (from repo root) | Needs | What it checks |
|---|---|---|
| `cd backend && .venv/Scripts/python eval_conversations.py` | Nebius key for hybrid | 4 realistic calls (`demo/conversations.json`, 69 turns with greetings and rapport): gate recall/precision and calls saved, top doc for every needed answer, rule verdicts |
| `backend/.venv/Scripts/python tools/test_audio.py [--call NAME] [--calibrate]` | keys + running backend | a whole call spoken by two ElevenLabs voices into ONE audio stream: speaker per turn, cards only where needed, API calls, speech-end→card latency |
| `cd backend && .venv/Scripts/python test_golden.py` | nothing | golden demo, split sentences, multi-ask turns, manual speaker flip |
| `cd backend && .venv/Scripts/python test_trigger.py` | nothing | instant extractor + wording-based speaker cues |
| `cd backend && .venv/Scripts/python test_retrieval.py` | Nebius key for hybrid | paraphrased questions → right doc |
| `backend/.venv/Scripts/python tools/check_keys.py` | keys | lists Nebius models, times them; tests TTS + batch + realtime STT |

## Models (all via the Nebius key)

| Role | Model | Notes |
|---|---|---|
| Understand / verify | `openai/gpt-oss-120b` | `reasoning_effort=low`; ~400–500ms; only on turns that pass the gate |
| Answer cards + summary | `openai/gpt-oss-120b` (`SMART_MODEL`) | any model from `tools/check_keys.py`'s list works |
| Embeddings | auto-picked (`Qwen/Qwen3-Embedding-8B`) | only on turns that pass the gate |
| Voiceprints | WeSpeaker ResNet34 (local, sherpa-onnx) | no API call |

## Team
1. Yash Chetan Doshi
2. Ritik Ashok Manghani
3. Sarvesh Shirude

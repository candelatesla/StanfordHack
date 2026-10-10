# DealBrain

Live sales copilot: listens to a call, checks customer asks against a Company Brain (docs + deterministic rules),
shows the rep an approved answer + counteroffer in <500ms, applies accepted terms to the CRM and contract, and writes a post-call summary.

## Run locally

```bash
cp .env.example .env            # keys optional: no keys = rules-only text mode
# backend
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/ on mac/linux)
.venv/Scripts/python -m uvicorn main:app --reload --port 8000
# frontend (second terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173
```

## Tests and tools

| Command (from repo root) | Needs | What it checks |
|---|---|---|
| `cd backend && .venv/Scripts/python test_golden.py` | nothing | 4 golden lines → right verdicts, CRM/contract, summary, split-turn merge |
| `cd backend && .venv/Scripts/python test_trigger.py` | nothing | regex extractor on STT-style phrasings ("net sixty", "three times fees") |
| `backend/.venv/Scripts/python tools/check_keys.py` | keys | lists Nebius models, times them, picks FAST/SMART/EMBED; tests TTS + batch + realtime STT |
| `backend/.venv/Scripts/python tools/test_audio.py` | keys + running backend | TTS golden lines streamed into `/ws/audio` → expects the right cards; prints speech-end→card latency |
| `backend/.venv/Scripts/python demo/make_audio.py` | ElevenLabs key | pre-generates the customer MP3s |

## Demo pages

- `/` : the rep's copilot (laptop 1). **Start listening** → pick the Meet tab → tick "Also share tab audio".
- `/customer` : soundboard for laptop 2. Plays each scripted line as ElevenLabs TTS into the Meet, or "Type it" to send as text.

Single-port mode (also what Replit runs): `bash start.sh` → http://localhost:8000

## How it works

| Step | File | Notes |
|---|---|---|
| STT | `backend/stt.py` | ElevenLabs Scribe v2 realtime; reconnects automatically if the upstream socket drops, falls back to VAD + batch STT. Tab audio = customer, mic = rep |
| Trigger | `backend/agent/trigger.py` | **Fast path:** regex extractor (~0ms) for recognised asks, then `FAST_MODEL` verifies in the background and revises the card if it disagrees. **Long tail:** `FAST_MODEL` extracts (≤1.5s budget) |
| Retrieve | `backend/agent/retrieve.py`, `brain/ingest.py` | In-memory numpy cosine over Nebius embeddings (fast-path queries pre-embedded; long-tail search runs in parallel with the trigger); keyword fallback |
| Rules | `backend/agent/rules.py` + `brain/rules.json` | **Authoritative.** The LLM never decides what's allowed |
| Respond | `backend/agent/respond.py` | `SMART_MODEL` streams `ANSWER:` / `SAY:`; deterministic template fallback |
| CRM / contract | `backend/crm.py`, `backend/contract.py` | SQLite + clause-level diff |
| Summary | `backend/summary.py` | Deterministic facts + optional LLM recap |

Every card shows the brain latency (trigger + retrieve + rules); hover it for the per-step breakdown.

Extra event-socket messages beyond the plan's contract: `init` (state snapshot on connect), `reset` (client → server, resets the demo deal), `suggestion_resolved`.

## Models (all via the Nebius key)

| Role | Model | Notes |
|---|---|---|
| Trigger / verify | `openai/gpt-oss-120b` | `reasoning_effort=low`; ~400–500ms per call |
| Answer cards + summary | `openai/gpt-oss-120b` (`SMART_MODEL`) | any model from `tools/check_keys.py`'s list works |
| Embeddings | auto-picked (`Qwen/Qwen3-Embedding-8B`) | ~390ms per query, so fast-path queries are pre-embedded at startup |

## Measured latency (tools/test_audio.py, ElevenLabs voice → realtime STT → card)

| Golden line | Brain (trigger+retrieve+rules) | Speech end → card on screen |
|---|---|---|
| 20% off for 3 years | 7ms | 612ms |
| HIPAA BAA | 6ms | 513ms |
| Net-60 | 7ms | 733ms |
| 3x liability cap | 7ms | 862ms |

Nearly all of the speech-end→card time is STT end-of-turn detection (`vad_silence_threshold_secs=0.6`).

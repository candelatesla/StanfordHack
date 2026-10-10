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

Smoke test the golden demo (text mode, works with or without keys): `cd backend && .venv/Scripts/python test_golden.py`

Single-port mode (also what Replit runs): `bash start.sh` → http://localhost:8000

## How it works

| Step | File | Notes |
|---|---|---|
| STT | `backend/stt.py` | ElevenLabs realtime; auto-falls back to VAD + batch STT. Tab audio = customer, mic = rep |
| Trigger | `backend/agent/trigger.py` | `FAST_MODEL` JSON extraction; regex heuristic when no key / on failure |
| Retrieve | `backend/agent/retrieve.py`, `brain/ingest.py` | In-memory numpy cosine over Nebius embeddings; keyword fallback |
| Rules | `backend/agent/rules.py` + `brain/rules.json` | **Authoritative.** The LLM never decides what's allowed |
| Respond | `backend/agent/respond.py` | `SMART_MODEL` streams `ANSWER:` / `SAY:`; deterministic template fallback |
| CRM / contract | `backend/crm.py`, `backend/contract.py` | SQLite + clause-level diff |
| Summary | `backend/summary.py` | Deterministic facts + optional LLM recap |

Every card shows the brain latency (trigger + retrieve + rules); hover it for the per-step breakdown.

Extra event-socket messages beyond the plan's contract: `init` (state snapshot on connect), `reset` (client → server, resets the demo deal), `suggestion_resolved`.

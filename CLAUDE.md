# DealBrain — notes for Claude sessions

Hackathon project (Track 4: "Sales without 'let me get back to you'"). A live sales copilot: listens to a call,
identifies who is speaking, ignores small talk, and when the customer asks for something or raises a concern it
checks the Company Brain (docs + deterministic rules) and shows the rep an approved answer + a line to say.
Accepting a card updates a mock CRM + contract; End call produces a post-call summary. Fake seller: Nimbus Analytics;
demo customer: Acme Health. See `README.md` for the user-facing overview and measured results.

## Run

```bash
# backend (Windows venv paths; use bin/ on mac/linux)
cd backend && .venv/Scripts/python -m uvicorn main:app --port 8000      # serves frontend/dist too
# frontend dev server (proxies /api and /ws to :8000)
cd frontend && npm run dev                                               # :5173
# after frontend changes, rebuild so :8000 serves them
cd frontend && npm run build
```

- Keys live in `D:\Stan_Hack\.env` (gitignored): `NEBIUS_API_KEY`, `ELEVENLABS_API_KEY`. Everything degrades
  gracefully without them (regex extractor, BM25 search, template answers, typed turns only).
- Voice model (26.5 MB, gitignored) must exist at `backend/models/`: `backend/.venv/Scripts/python tools/get_voice_model.py`.
- The backend does NOT hot-reload in the way we run it: **restart uvicorn after backend changes**. To free the port on
  Windows: `powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8000 -State Listen | % { Stop-Process -Id $_.OwningProcess -Force }"`.

## Tests (run from repo root unless noted; no pytest, plain scripts)

| Command | Keys? | Covers |
|---|---|---|
| `cd backend && .venv/Scripts/python test_golden.py` | optional | golden demo, split sentences, multi-ask turns, manual speaker flip (run with and without keys: `NEBIUS_API_KEY= ELEVENLABS_API_KEY= ...`) |
| `cd backend && .venv/Scripts/python test_trigger.py` | no | regex extractor + wording-based speaker cues |
| `cd backend && .venv/Scripts/python test_retrieval.py` | Nebius | paraphrased questions → right doc |
| `cd backend && .venv/Scripts/python eval_conversations.py` | Nebius | 4 labelled calls in `demo/conversations.json`: gate recall/precision, doc accuracy, rule verdicts |
| `backend/.venv/Scripts/python tools/test_audio.py [--call NAME] [--calibrate] [--meet [--echo]]` | both + running server | full call in two TTS voices into ONE audio socket (or two with `--meet`: tab = customer, mic = rep; `--echo` leaks customer audio into the mic); speaker accuracy, cards, API calls, latency (~4 min) |
| `backend/.venv/Scripts/python tools/check_keys.py` | both | lists Nebius models, times them, tests TTS + STT |

Set `PYTHONIOENCODING=utf-8` when running on Windows (cp1252 console chokes on ≥, ’, etc.).
Never click "New call"/↺ in the UI while `tools/test_audio.py` runs: the server holds a single shared call session.

## Architecture (backend/)

- `main.py` — FastAPI: `WS /ws/events` (UI events), `WS /ws/audio?speaker=customer|rep|auto` (16 kHz PCM16),
  `handle_turn()` decides the speaker (source label, calibration, voice, short-fragment continuation, wording).
- `session.py` — single in-memory call (transcript, cards, voice roster, stats, `call_id` to drop late work after reset).
- `stt.py` — ElevenLabs Scribe v2 realtime; `ResilientSTT` reconnects on drop *or silent stall* and replays audio since
  the last committed sentence; batch STT fallback with energy VAD.
- `voice.py` — voiceprints (WeSpeaker ResNet34 via sherpa-onnx, local), online clustering + merging, roles decided
  jointly across voices from calibration / manual pins / LLM votes / strong wording cues.
- `agent/gate.py` — free local check: does this turn deserve any API call? (recognised ask, or question/request/concern
  on Company Brain vocabulary). Small talk → nothing.
- `agent/trigger.py` — regex extractor (~30 situations, all asks per turn) + `llm_extract` (GPT-OSS, JSON, speaker + asks).
- `agent/pipeline.py` — gate → speaker → asks → per-ask (hybrid retrieve ∥ rules) → respond (streamed) → reconcile with LLM.
- `agent/retrieve.py`, `brain/ingest.py` — sentence-level hybrid search: utterance embedding (0.8) + sqrt(BM25) (0.2).
- `agent/rules.py` + `brain/rules.json` — **authoritative** decisions; the LLM never decides what's allowed.
- `agent/respond.py` — answer + "say" line; knowledge questions answered from sources.
- `crm.py` (SQLite: accounts, deals past + open, calls, change log; reseeded on server start; "reset" = restart the
  call and undo its CRM changes via `undo_call`), `contract.py` (clause diff), `summary.py` (End call also logs the call).
- `brain/docs/*.md` — 25 docs; keep them consistent with `rules.json` (docs give the snippet, rules give the decision).

Frontend (`frontend/src`): `components/Setup.jsx` (pre-call: existing customer search + dashboard, or new customer
form; Start call -> `POST /api/accounts/{id}/call`), `App.jsx` (reducer over events, audio lifecycle), `audio/capture.js` (devices opened once per
page session; pause = stop sending, not stop tracks; socket auto-reconnect), components for transcript, cards, deal, diff,
summary, `/customer` soundboard.

## Decisions already made (don't relitigate)

- Models via **Nebius only**: `FAST_MODEL`/`SMART_MODEL` = `openai/gpt-oss-120b` (`reasoning_effort=low`), embeddings
  auto-picked (`Qwen/Qwen3-Embedding-8B`). The user wanted Opus 5.5 as the smart model, but Nebius doesn't serve
  Claude models; still open whether to switch to a larger Nebius model or add an Anthropic key (user hasn't decided).
- Speaker ID must be voice-based in single-mic mode (user rejected keyword-only detection). Call-tab mode ("Call tab +
  mic", the default) uses the source: tab audio = customer, mic = rep; rep-mic turns that repeat recent customer words
  are dropped as speaker echo (`Session.is_echo`). Verified live on a real Google Meet call.
- Don't call APIs on small talk (user requirement: faster, cheaper). Keep the gate in front of every LLM/embedding call.
- Coverage must go beyond discounts: objections, security, integrations, procurement, etc.
- Audio permission is requested once per page session; Pause/End/New call must not re-prompt.

## Gotchas learned the hard way

- In this Windows Git-Bash tool, heredocs that contain `\\` or `\` line continuations get mangled, and long multi-heredoc
  commands can fail to parse. Prefer the Write/Edit tools for code; for scripted edits, assert the target string exists.
- React: never write `useEffect(() => el.scrollIntoView(...))` — newer Chrome returns a Promise, React calls it as the
  cleanup and the page goes blank. Use a block body.
- FastAPI `TestClient`: always use `with client.websocket_connect(...) as ws:`; `__enter__` without `__exit__` hangs on exit.
  Run all scenarios inside one `TestClient` (the AsyncOpenAI client is bound to its event loop).
- Cards are re-emitted with the same id (streaming, LLM verification, source upgrade); tests must track handled ids.
- ElevenLabs realtime can stall silently ("no close frame"); silence alone is not a stall — detection is based on
  unanswered *speech* (see `RealtimeSTT.stalled`).
- ElevenLabs legacy voice ids Adam/Antoni/Arnold sound alike to the voice model; tests use Rachel (customer) and Josh (rep).
- Gate/eval thresholds were tuned on `demo/conversations.json`; add new labelled calls there before re-tuning.

## Open items

- Replit deploy (`.replit` + `start.sh` exist; needs the user's Replit account, reserved VM not autoscale).
- 5-slide pitch deck; backup demo video.
- Smart-model decision (see above).

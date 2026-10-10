"""DealBrain backend: FastAPI + WebSockets.

  WS /ws/events          UI <-> backend events (transcript, suggestion, deal_update, escalation, summary)
  WS /ws/audio?speaker=  16kHz mono PCM16 from the browser (customer = tab audio, rep = mic)
"""
import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import config
import contract
import crm
import demo
import stt
import summary
from agent.llm import warmup
from agent.pipeline import on_final_customer_turn
from brain.ingest import INDEX, build_index
from session import SESSION

_tasks: set[asyncio.Task] = set()


def spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    t.add_done_callback(lambda t: t.exception() and print(f"[task] error: {t.exception()!r}") if not t.cancelled() else None)
    return t


@asynccontextmanager
async def lifespan(app: FastAPI):
    crm.init()
    SESSION.reset()
    await build_index()
    if config.LLM_ENABLED:
        await warmup()
    print(f"[startup] LLM {'enabled: ' + config.FAST_MODEL + ' / ' + config.SMART_MODEL if config.LLM_ENABLED else 'disabled (heuristic mode)'}")
    print(f"[startup] STT {'enabled (' + config.STT_MODE + ')' if config.ELEVENLABS_API_KEY else 'disabled (no ELEVENLABS_API_KEY)'}")
    yield


app = FastAPI(title="DealBrain", lifespan=lifespan)


def deal_snapshot() -> dict:
    deal = SESSION.deal
    return {"deal": deal, "contract": contract.clauses(deal),
            "contract_diff": contract.diff(SESSION.initial_deal, deal)}


@app.get("/api/health")
async def health():
    return {"ok": True, "llm": config.LLM_ENABLED, "stt": bool(config.ELEVENLABS_API_KEY),
            "index": "embeddings" if INDEX.vectors is not None else "keyword", "chunks": len(INDEX.chunks)}


@app.get("/api/deal")
async def get_deal():
    return deal_snapshot()


async def handle_customer_or_rep_turn(speaker: str, text: str):
    turn = SESSION.add_turn(speaker, text)
    await SESSION.emit(turn)
    if speaker == "customer":
        spawn(on_final_customer_turn(SESSION, text))


async def accept(sid: str):
    card = SESSION.suggestions.get(sid)
    if not card or sid in SESSION.accepted:
        return
    SESSION.accepted.append(sid)
    card["resolution"] = "accepted"
    old, new = crm.update_deal(SESSION.deal_id, card.get("proposed_changes") or {})
    await SESSION.emit({"type": "suggestion_resolved", "id": sid, "resolution": "accepted"})
    await SESSION.emit({"type": "deal_update", "id": sid, "fields": new, "contract": contract.clauses(new),
                        "changes": contract.diff(old, new),
                        "contract_diff": contract.diff(SESSION.initial_deal, new)})


async def escalate(sid: str):
    card = SESSION.suggestions.get(sid)
    if not card or sid in SESSION.escalated:
        return
    SESSION.escalated.append(sid)
    card["resolution"] = "escalated"
    await SESSION.emit({"type": "suggestion_resolved", "id": sid, "resolution": "escalated"})
    await SESSION.emit({"type": "escalation", "id": sid, "to": card.get("approval_needed") or "Sales Manager",
                        "reason": card.get("reason") or card.get("request"), "ts": time.time()})


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    SESSION.clients.add(ws)
    await ws.send_json({"type": "init", **deal_snapshot(), "transcript": SESSION.transcript,
                        "suggestions": list(SESSION.suggestions.values()),
                        "status": {"llm": config.LLM_ENABLED, "stt": bool(config.ELEVENLABS_API_KEY)}})
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "text_turn":
                text = (msg.get("text") or "").strip()
                if text:
                    await handle_customer_or_rep_turn(msg.get("speaker", "customer"), text)
            elif kind == "accept":
                await accept(msg["id"])
            elif kind == "escalate":
                await escalate(msg["id"])
            elif kind == "end_call":
                await SESSION.emit(await summary.build(SESSION))
            elif kind == "reset":
                SESSION.reset()
                await SESSION.emit({"type": "init", **deal_snapshot(), "transcript": [], "suggestions": [],
                                    "status": {"llm": config.LLM_ENABLED, "stt": bool(config.ELEVENLABS_API_KEY)}})
    except WebSocketDisconnect:
        pass
    finally:
        SESSION.clients.discard(ws)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket, speaker: str = "customer"):
    await ws.accept()
    if not config.ELEVENLABS_API_KEY:
        await ws.close(code=1011, reason="ELEVENLABS_API_KEY not set")
        return

    async def on_text(text: str, is_final: bool):
        if is_final:
            await handle_customer_or_rep_turn(speaker, text)
        else:
            await SESSION.emit({"type": "transcript", "speaker": speaker, "text": text,
                                "is_final": False, "ts": time.time()})

    stream = await stt.open_stream(on_text)
    try:
        while True:
            pcm = await ws.receive_bytes()
            await stream.feed(pcm)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await stream.close()


@app.get("/api/demo/lines")
async def demo_lines():
    return {"lines": demo.LINES, "tts": bool(config.ELEVENLABS_API_KEY)}


@app.get("/api/demo/audio/{i}.mp3")
async def demo_audio(i: int):
    if not 0 <= i < len(demo.LINES):
        raise HTTPException(404)
    if not config.ELEVENLABS_API_KEY and not (demo.AUDIO_DIR / f"{i}.mp3").exists():
        raise HTTPException(503, "ELEVENLABS_API_KEY not set")
    return Response(await demo.line_audio(i), media_type="audio/mpeg")


if config.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        f = config.FRONTEND_DIST / path
        return FileResponse(f if path and f.is_file() else config.FRONTEND_DIST / "index.html")

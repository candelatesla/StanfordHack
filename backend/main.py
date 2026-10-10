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
import voice
from agent.llm import warmup
from agent import speaker as speaker_id
from agent.pipeline import process_turn
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


def status() -> dict:
    return {"llm": config.LLM_ENABLED, "stt": bool(config.ELEVENLABS_API_KEY), "voice": voice.MODEL.available}


def deal_snapshot() -> dict:
    deal = SESSION.deal
    return {"deal": deal, "contract": contract.clauses(deal),
            "contract_diff": contract.diff(SESSION.initial_deal, deal)}


@app.get("/api/health")
async def health():
    return {"ok": True, "llm": config.LLM_ENABLED, "stt": bool(config.ELEVENLABS_API_KEY),
            "index": "hybrid" if INDEX.vectors is not None else "bm25", "chunks": len(INDEX.chunks),
            "sentences": len(INDEX.sentences), "voice": voice.MODEL.available or voice.MODEL.error}


@app.get("/api/deal")
async def get_deal():
    return deal_snapshot()


async def handle_turn(speaker: str, text: str, voiceprint=None):
    """speaker: "customer"/"rep" when the audio source tells us (Meet tab / rep mic), else "auto".
    voiceprint: this utterance's voice embedding (single-mic audio), used to tell people apart."""
    if speaker == "auto":
        roster = SESSION.roster
        vp, seconds = voiceprint if voiceprint is not None else (None, 0.0)
        vid, sim = roster.identify(vp, seconds) if vp is not None else (None, 0.0)
        if vid is not None:
            print(f"[voice] speaker {vid} sim={sim:.2f} role={roster.role(vid)} | {text[:50]!r}")
        if vid is not None and time.time() < SESSION.calibrate_until:
            roster.pin(vid, "rep")
            SESSION.calibrate_until = 0
            turn = SESSION.add_turn("rep", text, auto=True, confident=True)
            turn["via"] = "calibration"
            await SESSION.emit({"type": "calibrated", "voice_id": vid})
        else:
            voice_role, strength = roster.role(vid)
            guess, score = speaker_id.guess(text, SESSION.transcript[-6:], list(SESSION.suggestions.values()))
            if voice_role and strength >= voice.ESTABLISHED:
                # this voice's role is known: trust the voice, not the wording
                turn = SESSION.add_turn(voice_role, text, auto=True, confident=True)
                turn["via"] = "voice"
            else:
                confident = score >= speaker_id.CONFIDENT or not config.LLM_ENABLED
                turn = SESSION.add_turn(guess, text, auto=True, confident=confident)
                turn["via"] = "wording"
                if score >= speaker_id.CONFIDENT:  # strong cues (e.g. reading the card aloud) count more
                    roster.vote(vid, guess, min(score / 2.0, 2.5))
        turn["voice_id"] = vid
    else:
        if speaker == "rep" and SESSION.is_echo(text):
            print(f"[turn] dropped rep-mic echo of customer audio: {text!r}")
            return
        turn = SESSION.add_turn(speaker, text)
    await SESSION.emit(turn)
    spawn(process_turn(SESSION, turn))


async def set_speaker(turn_id: str, speaker: str):
    """Manual correction from the UI (click the speaker label)."""
    turn = SESSION.turn(turn_id)
    if not turn or speaker not in ("customer", "rep"):
        return
    was = turn["speaker"]
    SESSION.roster.pin(turn.get("voice_id"), speaker)  # a correction also teaches the voice its role
    turn["via"] = "manual"
    await SESSION.set_speaker(turn, speaker, manual=True)
    await SESSION.emit_stats()
    if speaker == "rep" and was == "customer":
        for sid, card in list(SESSION.suggestions.items()):
            if card.get("turn_id") == turn_id and not card.get("resolution"):
                await SESSION.remove_card(sid)
    elif speaker == "customer" and was == "rep":
        spawn(process_turn(SESSION, turn))


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
                        "status": status(), "stats": {**SESSION.stats, "voices": SESSION.roster.summary()}})
    try:
        while True:
            msg = await ws.receive_json()
            kind = msg.get("type")
            if kind == "text_turn":
                text = (msg.get("text") or "").strip()
                if text:
                    await handle_turn(msg.get("speaker", "auto"), text)
            elif kind == "calibrate":
                SESSION.calibrate_until = time.time() + 15
            elif kind == "set_speaker":
                await set_speaker(msg.get("id"), msg.get("speaker"))
            elif kind == "accept":
                await accept(msg["id"])
            elif kind == "escalate":
                await escalate(msg["id"])
            elif kind == "end_call":
                await SESSION.emit(await summary.build(SESSION))
            elif kind == "reset":
                SESSION.reset()
                await SESSION.emit({"type": "init", **deal_snapshot(), "transcript": [], "suggestions": [],
                                    "status": status(), "stats": {**SESSION.stats, "voices": []}})
    except WebSocketDisconnect:
        pass
    finally:
        SESSION.clients.discard(ws)


@app.websocket("/ws/audio")
async def ws_audio(ws: WebSocket, speaker: str = "auto"):
    await ws.accept()
    if not config.ELEVENLABS_API_KEY:
        await ws.close(code=1011, reason="ELEVENLABS_API_KEY not set")
        return

    voiced = voice.VoicedBuffer() if speaker == "auto" else None  # one mic, two people: identify by voice

    async def on_text(text: str, is_final: bool):
        if is_final:
            vp = None
            if voiced:
                clip = voiced.take()
                emb = await asyncio.to_thread(voice.MODEL.embed, clip)
                vp = (emb, len(clip) / 32000) if emb is not None else None
            await handle_turn(speaker, text, vp)
        else:
            await SESSION.emit({"type": "transcript", "speaker": speaker, "text": text,
                                "is_final": False, "ts": time.time()})

    stream = await stt.open_stream(on_text)
    try:
        while True:
            pcm = await ws.receive_bytes()
            if voiced:
                voiced.add(pcm)
            await stream.feed(pcm)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as e:
        print(f"[audio] {speaker} socket error: {e!r}")
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

"""Voice end-to-end test without a Meet call: a whole sales call from demo/conversations.json (small talk
included) is spoken by two ElevenLabs voices into ONE audio socket (speaker=auto), like a single mic.
--meet uses two sockets like call-tab mode (customer = tab audio, rep = mic); --echo also leaks the customer
into the rep mic (quieter, slightly late), like a rep on laptop speakers without headphones.

Checks, per turn: who the app thinks spoke (voice identification), that cards appear only where an
answer is needed, speech-end -> card latency; and for the call: how many API calls were made.

    backend/.venv/Scripts/python tools/test_audio.py [--call "Retail discovery call"] [--calibrate] [--meet [--echo]] [--url ...]
Needs ELEVENLABS_API_KEY and a running backend. TTS audio is cached in demo/customer_audio/cache/.
"""
import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import demo  # noqa: E402

CUSTOMER_VOICE = "21m00Tcm4TlvDq8ikWAM"
SILENCE = b"\0" * 32000  # 1s between turns so STT closes each one
CACHE = ROOT / "demo" / "customer_audio" / "cache"


async def speech(text: str, voice: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{hashlib.sha1(f'{voice}:{text}'.encode()).hexdigest()[:16]}.pcm"
    if not f.exists():
        f.write_bytes(await demo.tts(text, "pcm_16000", voice=voice))
    return f.read_bytes()


def bleed(pcm: bytes, gain=0.35, delay_s=0.15) -> bytes:
    """Customer audio as the rep's mic hears it from laptop speakers."""
    x = np.frombuffer(pcm, np.int16).astype(np.float32) * gain
    return b"\0" * (int(delay_s * 16000) * 2) + x.astype(np.int16).tobytes()


async def run(call: dict, url: str, calibrate: bool, meet: bool, echo: bool):
    base = url.replace("http", "ws", 1)
    async with websockets.connect(f"{base}/ws/events") as ev, \
            websockets.connect(f"{base}/ws/audio?speaker={'customer' if meet else 'auto'}") as audio, \
            websockets.connect(f"{base}/ws/audio?speaker=rep") as mic:
        await ev.send(json.dumps({"type": "reset"}))
        inbox: asyncio.Queue = asyncio.Queue()
        labels, texts, card_turn, stats = {}, {}, {}, {}

        async def reader():
            async for raw in ev:
                msg = json.loads(raw)
                if msg["type"] == "transcript" and msg.get("is_final"):
                    labels[msg["id"]] = msg["speaker"]
                    texts[msg["id"]] = msg["text"]
                elif msg["type"] == "transcript_update":
                    labels[msg["id"]] = msg["speaker"]
                elif msg["type"] == "stats":
                    stats.update(msg)
                await inbox.put((time.perf_counter(), msg))
        read_task = asyncio.create_task(reader())

        async def say(text: str, voice: str):
            pcm = await speech(text, voice) + SILENCE
            # single mic: everything on one socket. meet: rep -> mic socket, customer -> tab socket (+ bleed
            # into the mic with --echo); the idle socket gets silence, as the browser keeps streaming both.
            rep = voice == demo.REP_VOICE_ID
            main, other = (mic, audio) if meet and rep else (audio, mic)
            leak = bleed(pcm) if meet and echo and not rep else b""
            for j in range(0, len(pcm), 3200):  # real-time 100ms chunks, like the browser
                await main.send(pcm[j:j + 3200])
                if meet:
                    chunk = leak[j:j + 3200]
                    await other.send(chunk + b"\0" * (3200 - len(chunk)))
                await asyncio.sleep(0.1)
            return time.perf_counter() - len(SILENCE) / 32000

        if calibrate:
            await ev.send(json.dumps({"type": "calibrate"}))
            await say("Hi, this is Sam from Nimbus, just calibrating my voice for the copilot.", demo.REP_VOICE_ID)
            await asyncio.sleep(1.5)

        ok_speaker = ok_cards = 0
        lat = []
        for who, text, needs, _doc, _verdict in call["turns"]:
            t_end = await say(text, demo.REP_VOICE_ID if who == "rep" else CUSTOMER_VOICE)
            turn_ids, cards, t_card = [], set(), None
            deadline = time.perf_counter() + (8 if needs else 2.5)
            while time.perf_counter() < deadline:
                try:
                    t_msg, msg = await asyncio.wait_for(inbox.get(), max(0.01, deadline - time.perf_counter()))
                except asyncio.TimeoutError:
                    break
                if msg["type"] == "transcript" and msg.get("is_final"):
                    turn_ids.append(msg["id"])
                elif msg["type"] == "suggestion" and msg.get("turn_id") in turn_ids:
                    t_card = t_card or t_msg
                    cards.add(msg["id"])
                    if needs and msg["status"] == "done" and msg.get("check"):
                        deadline = min(deadline, time.perf_counter() + 0.5)
            await asyncio.sleep(0.3)  # let a late LLM speaker correction land
            got = {labels.get(t) for t in turn_ids}
            sp_ok = got == {who}
            card_ok = bool(cards) == needs
            ok_speaker += sp_ok
            ok_cards += card_ok
            if cards and t_card:
                lat.append((t_card - t_end) * 1000)
            flag = "OK " if sp_ok and card_ok else "BAD"
            print(f"[{flag}] {who:8} heard as {'/'.join(sorted(map(str, got))) or '-':9} "
                  f"cards={len(cards)} (needs {'yes' if needs else 'no '}) | {text[:62]}")
            for t in turn_ids:
                if labels.get(t) != who:
                    print(f"        heard as {labels.get(t)}: {texts.get(t)!r}")
        read_task.cancel()
        n = len(call["turns"])
        mode = ("call tab + mic" + (", speaker bleed" if echo else "")) if meet else \
            ("single mic, calibrated" if calibrate else "single mic, no calibration")
        print(f"\n{call['name']} ({mode}):")
        print(f"  speaker right {ok_speaker}/{n}   cards right {ok_cards}/{n}   "
              f"speech-end->card {min(lat, default=0):.0f}-{max(lat, default=0):.0f}ms")
        print(f"  API: {stats.get('llm_calls', 0)} LLM + {stats.get('embed_calls', 0)} embedding calls for "
              f"{stats.get('turns', 0)} possibly-customer turns; {stats.get('skipped', 0)} skipped by the gate; "
              f"voices {[(v['id'], v['role'], v['turns']) for v in stats.get('voices', [])]}; "
              f"{stats.get('echoes', 0)} mic echoes filtered")
        return ok_speaker, ok_cards, n


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--call", default="Acme Health negotiation")
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--meet", action="store_true", help="two sockets: tab audio = customer, mic = rep")
    ap.add_argument("--echo", action="store_true", help="with --meet: customer audio leaks into the rep mic")
    ap.add_argument("--url", default="http://localhost:8000")
    a = ap.parse_args()
    calls = json.loads((ROOT / "demo" / "conversations.json").read_text(encoding="utf-8"))["calls"]
    call = next(c for c in calls if c["name"] == a.call)
    s, c, n = await run(call, a.url, a.calibrate, a.meet, a.echo)
    print("AUDIO E2E OK" if s == n and c == n else "AUDIO E2E: see BAD lines")


asyncio.run(main())

"""End-to-end voice test without a Meet call. ElevenLabs TTS speaks a customer/rep conversation (two voices)
into ONE audio socket with speaker=auto, like a single mic. Checks speaker auto-detection, the cards,
and speech-end -> card latency.

Needs ELEVENLABS_API_KEY and a running backend:
    backend/.venv/Scripts/python tools/test_audio.py [http://localhost:8000]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import demo  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").replace("http", "ws", 1)
SILENCE = b"\0" * 48000  # 1.5s between turns so STT closes each one

SCRIPT = [  # (speaker, line, expected card verdict or None)
    ("rep", "Thanks for making time today. How's the evaluation going on your side?", None),
    ("customer", "Thanks for making time. We've reviewed the Enterprise proposal.", None),
    ("customer", "Can you do 20% off if we sign for 3 years?", "counter"),
    ("rep", "We can do fifteen percent with a three year commitment, and that's approved today.", None),
    ("customer", "We're in healthcare. Do you sign a HIPAA BAA?", "info"),
    ("rep", "Yes, the BAA is included in your Enterprise plan.", None),
    ("customer", "We need net-60 payment terms.", "approved"),
    ("customer", "Can you raise the liability cap to 3x fees?", "escalate"),
    ("rep", "Let me check with our Legal team and get back to you on the cap.", None),
]


async def main():
    async with websockets.connect(f"{BASE}/ws/events") as ev, \
            websockets.connect(f"{BASE}/ws/audio?speaker=auto") as audio:
        await ev.send(json.dumps({"type": "reset"}))
        inbox: asyncio.Queue = asyncio.Queue()

        async def reader():  # timestamp events as they arrive, while audio is still streaming
            async for raw in ev:
                await inbox.put((time.perf_counter(), json.loads(raw)))
        read_task = asyncio.create_task(reader())

        ok, seen = True, set()
        for who, line, want in SCRIPT:
            voice = demo.REP_VOICE_ID if who == "rep" else None
            pcm = await demo.tts(line, "pcm_16000", voice=voice) + SILENCE
            t_end = None
            for j in range(0, len(pcm), 3200):  # 100ms chunks in real time, like the browser
                await audio.send(pcm[j:j + 3200])
                await asyncio.sleep(0.1)
                if t_end is None and j + 3200 >= len(pcm) - len(SILENCE):
                    t_end = time.perf_counter()

            heard = labelled = card = t_card = turn_id = None
            deadline = time.perf_counter() + (12 if want else 4)
            while time.perf_counter() < deadline:
                try:
                    t_msg, msg = await asyncio.wait_for(inbox.get(), max(0.01, deadline - time.perf_counter()))
                except asyncio.TimeoutError:
                    break
                if msg["type"] == "transcript" and msg["is_final"]:
                    heard, labelled, turn_id = msg["text"], msg["speaker"], msg["id"]
                elif msg["type"] == "transcript_update" and msg["id"] == turn_id:
                    labelled = msg["speaker"]
                elif msg["type"] == "suggestion" and msg["id"] not in seen:
                    t_card = t_card or t_msg
                    if msg["status"] == "done" and msg.get("check"):
                        card = msg
                        seen.add(msg["id"])
                        break
            got = card["verdict"] if card else None
            good = labelled == who and got == want
            ok &= good
            lat = f" speech-end->card {(t_card - t_end) * 1000:.0f}ms" if t_card and card else ""
            print(f"[{'OK ' if good else 'FAIL'}] {who:8} -> labelled {labelled!s:8} card={got!s:8} (want {want}){lat}\n"
                  f"        heard: {heard!r}")
        read_task.cancel()
        print("AUDIO E2E OK" if ok else "AUDIO E2E FAILED")


asyncio.run(main())

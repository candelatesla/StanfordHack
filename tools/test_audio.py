"""End-to-end voice test without a Meet call: TTS the golden lines -> stream PCM into /ws/audio as the
customer -> expect transcript + suggestion cards on /ws/events.

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
EXPECT = {1: "counter", 2: "info", 3: "approved", 4: "escalate"}


async def main():
    async with websockets.connect(f"{BASE}/ws/events") as ev, \
            websockets.connect(f"{BASE}/ws/audio?speaker=customer") as audio:
        await ev.send(json.dumps({"type": "reset"}))
        ok, seen = True, set()
        inbox: asyncio.Queue = asyncio.Queue()

        async def reader():  # timestamp events as they arrive, while audio is still streaming
            async for raw in ev:
                await inbox.put((time.perf_counter(), json.loads(raw)))
        read_task = asyncio.create_task(reader())
        for i, line in enumerate(demo.LINES[:5]):
            pcm = await demo.tts(line, "pcm_16000") + b"\0" * 48000  # + 1.5s silence to end the turn
            t_end = None
            for j in range(0, len(pcm), 3200):  # 100ms chunks in real time, like the browser
                await audio.send(pcm[j:j + 3200])
                await asyncio.sleep(0.1)
                if j + 3200 >= len(pcm) - 48000 and t_end is None:
                    t_end = time.perf_counter()
            card, heard, t_card, t_heard = None, None, None, None
            deadline = time.perf_counter() + 12
            while time.perf_counter() < deadline:
                try:
                    t_msg, msg = await asyncio.wait_for(inbox.get(), max(0.01, deadline - time.perf_counter()))
                except asyncio.TimeoutError:
                    break
                if msg["type"] == "transcript" and msg["is_final"]:
                    heard, t_heard = msg["text"], t_msg
                    if i not in EXPECT:
                        break
                if msg["type"] == "suggestion" and msg["id"] not in seen:
                    t_card = t_card or t_msg  # first paint of the new card
                    if msg["status"] == "done" and msg.get("check"):
                        card = msg
                        seen.add(msg["id"])
                        break
            speech_end_to_card = f"{(t_card - t_end) * 1000:.0f}ms" if t_card else "-"
            if t_heard:
                speech_end_to_card += f" (STT final at +{(t_heard - t_end) * 1000:.0f}ms)"
            want = EXPECT.get(i)
            got = card["verdict"] if card else None
            status = "OK " if got == want else "FAIL"
            ok &= got == want
            print(f"[{status}] line {i}: heard={heard!r}\n        card={got} (want {want}) brain={card and card['latency_ms']}ms "
                  f"speech-end->card={speech_end_to_card}\n        say={card and card['counteroffer']!r}")
        read_task.cancel()
        print("AUDIO E2E OK" if ok else "AUDIO E2E FAILED")


asyncio.run(main())

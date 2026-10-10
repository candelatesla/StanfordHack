"""Pre-generate customer TTS lines: backend/.venv/Scripts/python demo/make_audio.py
(The /customer soundboard page also generates them on first play.)"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import demo  # noqa: E402


async def main():
    for i, line in enumerate(demo.LINES):
        await demo.line_audio(i)
        print(f"{i}.mp3  {line}")

asyncio.run(main())

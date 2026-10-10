"""Generate customer lines as MP3s with ElevenLabs TTS: python demo/make_audio.py [voice_id]"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
LINES = [
    "Thanks for making time. We've reviewed the Enterprise proposal.",
    "Can you do 20% off if we sign for 3 years?",
    "We're in healthcare. Do you sign a HIPAA BAA?",
    "We need net-60 payment terms.",
    "Can you raise the liability cap to 3x fees?",
    "Okay, that works for us. Send over the paperwork.",
]
voice = sys.argv[1] if len(sys.argv) > 1 else "21m00Tcm4TlvDq8ikWAM"  # any voice ID from your library
out = Path(__file__).parent / "customer_audio"
out.mkdir(exist_ok=True)
for i, line in enumerate(LINES):
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                   headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                   json={"text": line, "model_id": "eleven_multilingual_v2"}, timeout=60)
    r.raise_for_status()
    (out / f"{i}.mp3").write_bytes(r.content)
    print(f"{i}.mp3  {line}")

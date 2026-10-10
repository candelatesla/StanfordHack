"""Golden-demo customer lines + ElevenLabs TTS (cached to demo/customer_audio/)."""
import httpx

import config

LINES = [
    "Thanks for making time. We've reviewed the Enterprise proposal.",
    "Can you do 20% off if we sign for 3 years?",
    "We're in healthcare. Do you sign a HIPAA BAA?",
    "We need net-60 payment terms.",
    "Can you raise the liability cap to 3x fees?",
    "Okay, that works for us. Send over the paperwork.",
]
AUDIO_DIR = config.ROOT.parent / "demo" / "customer_audio"


async def tts(text: str, output_format: str = "mp3_44100_128") -> bytes:
    voice = config.TTS_VOICE_ID or "21m00Tcm4TlvDq8ikWAM"
    async with httpx.AsyncClient(timeout=60) as http:
        r = await http.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
                            params={"output_format": output_format},
                            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                            json={"text": text, "model_id": "eleven_flash_v2_5"})
        r.raise_for_status()
        return r.content


async def line_audio(i: int) -> bytes:
    path = AUDIO_DIR / f"{i}.mp3"
    if not path.exists():
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        path.write_bytes(await tts(LINES[i]))
    return path.read_bytes()

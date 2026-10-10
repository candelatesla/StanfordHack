"""Verify keys, pick models, measure latency.  Run from repo root:
    backend/.venv/Scripts/python tools/check_keys.py
Prints suggested .env lines at the end."""
import asyncio
import io
import statistics
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import config  # noqa: E402

FAST_HINTS = ["Meta-Llama-3.1-8B-Instruct", "Qwen3-4B", "Qwen2.5-7B", "Qwen3-8B", "gemma-2-9b", "Mistral-7B", "8B", "7B", "9B"]
SMART_HINTS = ["Llama-3.3-70B-Instruct", "Qwen3-32B", "Qwen2.5-72B", "Meta-Llama-3.1-70B", "gpt-oss-120b", "70B", "72B", "32B"]
NOT_CHAT = ("embed", "bge", "e5-", "vision", "VL", "guard", "Guard", "flux", "FLUX", "Coder", "Math")


def ms(t0):
    return (time.perf_counter() - t0) * 1000


async def time_chat(client, model, json_mode, n=3):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        kw = {"response_format": {"type": "json_object"}} if json_mode else {}
        r = await client.chat.completions.create(
            model=model, temperature=0, max_tokens=60,
            messages=[{"role": "system", "content": 'Return JSON {"needs_brain": bool, "discount_pct": number|null}.'},
                      {"role": "user", "content": "Can you do 20% off if we sign for 3 years?"}], **kw)
        times.append(ms(t0))
    return statistics.median(times), r.choices[0].message.content


async def check_nebius() -> dict:
    print(f"\n== Nebius ({config.NEBIUS_BASE_URL}) ==")
    if not config.NEBIUS_API_KEY:
        print("  NEBIUS_API_KEY missing")
        return {}
    from agent.llm import client, list_models, resolve_embed_model
    from brain.ingest import embed

    models = await list_models()
    print(f"  {len(models)} models visible:")
    for m in models:
        print("   -", m)

    def pick(hints):
        for h in hints:
            for m in models:
                if h.lower() in m.lower() and not any(x in m for x in NOT_CHAT):
                    return m

    env = {}
    for role, current, hints, json_mode in [("FAST_MODEL", config.FAST_MODEL, FAST_HINTS, True),
                                            ("SMART_MODEL", config.SMART_MODEL, SMART_HINTS, False)]:
        cands = [m for m in dict.fromkeys([current if current in models else None, pick(hints)]) if m]
        cands = [f"{m}-fast" if f"{m}-fast" in models else m for m in cands]  # prefer -fast variants
        for m in cands:
            try:
                med, out = await time_chat(client, m, json_mode)
                print(f"  {role}: {m}  median {med:.0f}ms  -> {out[:80]!r}")
                env[role] = m
                break
            except Exception as e:
                print(f"  {role}: {m} failed: {str(e)[:160]}")
        else:
            print(f"  {role}: no working model found; pick one from the list above")
    try:
        em = await resolve_embed_model()
        await embed(["warm"])
        t0 = time.perf_counter()
        v = await embed(["multi-year discount policy"])
        print(f"  EMBED_MODEL: {em}  {ms(t0):.0f}ms  dim={v.shape[1]}")
        env["EMBED_MODEL"] = em
    except Exception as e:
        print(f"  embeddings failed: {e}")
    return env


async def check_elevenlabs():
    print("\n== ElevenLabs ==")
    if not config.ELEVENLABS_API_KEY:
        print("  ELEVENLABS_API_KEY missing")
        return
    import httpx

    import demo
    import stt

    t0 = time.perf_counter()
    try:
        pcm = await demo.tts("We need net sixty payment terms.", "pcm_16000")
    except Exception as e:
        print(f"  TTS FAILED: {e}")
        return
    print(f"  TTS ok: {len(pcm) / 32000:.1f}s of audio in {ms(t0):.0f}ms")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30) as http:
        r = await http.post(stt.BatchSTT.URL, headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                            data={"model_id": config.STT_BATCH_MODEL},
                            files={"file": ("t.wav", buf.getvalue(), "audio/wav")})
    print(f"  batch STT {r.status_code} in {ms(t0):.0f}ms: {r.json().get('text') if r.is_success else r.text[:200]}")

    got, texts = asyncio.Event(), []

    async def on_text(text, final):
        if final:
            texts.append(text)
            got.set()

    try:
        s = stt.RealtimeSTT(on_text)
        await s.start()
        audio = pcm + b"\0" * 48000  # trailing 1.5s silence so VAD commits
        for i in range(0, len(audio), 3200):
            await s.feed(audio[i:i + 3200])
            await asyncio.sleep(0.1)
        await asyncio.wait_for(got.wait(), 10)
        print(f"  realtime STT ok: {texts[0]!r}")
        await s.close()
    except Exception as e:
        print(f"  realtime STT FAILED ({e!r}); app falls back to batch. Set STT_MODE=batch to skip the attempt.")


async def main():
    env = await check_nebius()
    await check_elevenlabs()
    if env:
        print("\n== Suggested .env lines ==")
        for k, v in env.items():
            print(f"{k}={v}")


asyncio.run(main())

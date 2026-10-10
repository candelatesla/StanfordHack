"""Shared Nebius Token Factory client (OpenAI-compatible) + model discovery/warmup."""
import time

from openai import AsyncOpenAI

import config

client = AsyncOpenAI(api_key=config.NEBIUS_API_KEY, base_url=config.NEBIUS_BASE_URL,
                     timeout=20, max_retries=1) if config.LLM_ENABLED else None

_EMBED_HINTS = ("embed", "bge", "e5-", "gte", "qwen3-embedding", "nomic")


async def list_models() -> list[str]:
    return sorted(m.id for m in (await client.models.list()).data)


async def resolve_embed_model() -> str:
    """Use EMBED_MODEL if set, otherwise pick the first embedding-looking model the account can see."""
    if config.EMBED_MODEL:
        return config.EMBED_MODEL
    candidates = [m for m in await list_models() if any(h in m.lower() for h in _EMBED_HINTS)]
    if not candidates:
        raise RuntimeError("no embedding model found; set EMBED_MODEL")
    config.EMBED_MODEL = candidates[0]
    print(f"[llm] auto-selected EMBED_MODEL={config.EMBED_MODEL}")
    return config.EMBED_MODEL


async def warmup():
    """One tiny call per chat model so the first real turn doesn't pay connection/cold-start cost."""
    for model in dict.fromkeys([config.FAST_MODEL, config.SMART_MODEL]):
        t0 = time.perf_counter()
        try:
            await client.chat.completions.create(model=model, max_tokens=1,
                                                 messages=[{"role": "user", "content": "hi"}])
            print(f"[llm] warm {model}: {(time.perf_counter() - t0) * 1000:.0f}ms")
        except Exception as e:
            print(f"[llm] warmup failed for {model}: {e}")

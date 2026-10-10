"""Shared Nebius Token Factory client (OpenAI-compatible)."""
from openai import AsyncOpenAI

import config

client = AsyncOpenAI(api_key=config.NEBIUS_API_KEY, base_url=config.NEBIUS_BASE_URL) if config.LLM_ENABLED else None

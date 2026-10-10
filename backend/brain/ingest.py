"""Chunk the markdown knowledge base and embed it into an in-memory index at startup."""
import math
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

import config
from agent.llm import client, resolve_embed_model


@dataclass
class Chunk:
    title: str
    file: str
    text: str


@dataclass
class Index:
    chunks: list[Chunk] = field(default_factory=list)
    vectors: np.ndarray | None = None  # Nebius embeddings, L2-normalised
    bow: list[Counter] = field(default_factory=list)  # keyword fallback
    idf: dict[str, float] = field(default_factory=dict)


INDEX = Index()
_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def load_chunks() -> list[Chunk]:
    chunks = []
    for path in sorted(config.DOCS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        title_match = re.search(r"^#\s+(.+)$", raw, re.M)
        title = title_match.group(1).strip() if title_match else path.stem
        body = re.sub(r"^#.*$", "", raw, flags=re.M)
        for para in (p.strip() for p in body.split("\n\n")):
            if para:
                chunks.append(Chunk(title=title, file=path.name, text=para.replace("**", "")))
    return chunks


async def embed(texts: list[str]) -> np.ndarray:
    resp = await client.embeddings.create(model=config.EMBED_MODEL, input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


async def build_index() -> Index:
    INDEX.chunks = load_chunks()
    INDEX.bow = [Counter(tokens(f"{c.title} {c.text}")) for c in INDEX.chunks]
    df = Counter(t for bag in INDEX.bow for t in bag)
    n = len(INDEX.bow)
    INDEX.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    if client is not None:
        try:
            await resolve_embed_model()
            INDEX.vectors = await embed([f"{c.title}: {c.text}" for c in INDEX.chunks])
        except Exception as e:  # keep the demo alive on keyword search
            print(f"[ingest] embeddings failed, using keyword search: {e}")
            INDEX.vectors = None
    mode = "embeddings" if INDEX.vectors is not None else "keyword"
    print(f"[ingest] {len(INDEX.chunks)} chunks indexed ({mode})")
    return INDEX

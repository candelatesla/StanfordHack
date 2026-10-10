"""Chunk the markdown knowledge base into paragraphs and sentences; index both ways at startup.

- Sparse: BM25 over sentences (title-boosted) -> exact, keyword-sensitive matches ("net 60", "BAA").
- Dense: Nebius embeddings per sentence -> meaning of the whole utterance, paraphrases, context.
Retrieval fuses the two at sentence level and rolls up to the paragraph (see agent/retrieve.py).
"""
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
class Sentence:
    chunk: int
    text: str


@dataclass
class Index:
    chunks: list[Chunk] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)
    vectors: np.ndarray | None = None          # one L2-normalised embedding per sentence
    bags: list[Counter] = field(default_factory=list)
    idf: dict[str, float] = field(default_factory=dict)
    avg_len: float = 1.0


INDEX = Index()
_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = set("""a an the and or but if of to in on for with at by from as is are was were be been being it its
this that these those we you they i our your their us me my do does did can could would should will may might
must have has had not no so than then there here what which who whom how about into over under up down out
any all each other some such only own same too very just also per via""".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


def split_sentences(para: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*])", para) if s.strip()]


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
    INDEX.sentences = [Sentence(i, s) for i, c in enumerate(INDEX.chunks) for s in split_sentences(c.text)]
    # title tokens count twice so "HIPAA" in a doc title anchors all its sentences
    INDEX.bags = [Counter(tokens(f"{INDEX.chunks[s.chunk].title} " * 2 + s.text)) for s in INDEX.sentences]
    n = len(INDEX.bags)
    df = Counter(t for bag in INDEX.bags for t in bag)
    INDEX.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}  # BM25 idf
    INDEX.avg_len = sum(sum(b.values()) for b in INDEX.bags) / max(n, 1)
    if client is not None:
        try:
            await resolve_embed_model()
            INDEX.vectors = await embed([f"{INDEX.chunks[s.chunk].title}: {s.text}" for s in INDEX.sentences])
        except Exception as e:  # keep the demo alive on keyword search
            print(f"[ingest] embeddings failed, using keyword search only: {e}")
            INDEX.vectors = None
    mode = "hybrid (BM25 + embeddings)" if INDEX.vectors is not None else "BM25 only"
    print(f"[ingest] {len(INDEX.chunks)} paragraphs / {len(INDEX.sentences)} sentences indexed, {mode}")
    return INDEX

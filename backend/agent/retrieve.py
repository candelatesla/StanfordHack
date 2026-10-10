"""Hybrid retrieval: the whole utterance (dense) + its keywords (BM25), fused per sentence, rolled up
to paragraphs. The card quotes the best-matching sentences, not a whole doc."""
import asyncio
import time

import numpy as np

from agent.llm import client
from brain.ingest import INDEX, embed, tokens

DENSE_WEIGHT = 0.8  # tuned on test_retrieval.py paraphrases: 0.6 -> 5/8, 0.8 -> 7/8 docs
K1, B = 1.2, 0.75
_cache: dict[str, np.ndarray] = {}


async def embed_query(text: str) -> np.ndarray | None:
    """Start this as soon as a turn arrives; retrieval awaits it within its latency budget."""
    if client is None or INDEX.vectors is None:
        return None
    if text not in _cache:
        try:
            _cache[text] = (await embed([text]))[0]
        except Exception as e:
            print(f"[retrieve] query embedding failed: {e}")
            return None
    return _cache[text]


def bm25(query: str) -> np.ndarray:
    q = set(tokens(query))
    scores = np.zeros(len(INDEX.bags), dtype=np.float32)
    for i, bag in enumerate(INDEX.bags):
        dl = sum(bag.values())
        for t in q:
            f = bag.get(t, 0)
            if f:
                scores[i] += INDEX.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / INDEX.avg_len))
    return scores


def _norm(x: np.ndarray) -> np.ndarray:
    lo, hi = float(x.min()), float(x.max())
    return (x - lo) / (hi - lo) if hi > lo else np.zeros_like(x)


async def retrieve(text: str, keywords: str = "", qvec_task: asyncio.Task | None = None,
                   deadline: float | None = None, k: int = 2, dense_weight: float = DENSE_WEIGHT) -> list[dict]:
    """text: what the customer said (merged with context when it was a fragment).
    keywords: category hint from the extractor (steers the sparse side).
    qvec_task: in-flight embed_query(text); used if it lands before `deadline` (perf_counter)."""
    if not INDEX.sentences:
        return []
    sparse = np.sqrt(bm25(f"{text} {keywords}"))  # damp single generic-word matches
    qvec = None
    if qvec_task is not None:
        try:
            wait = None if deadline is None else max(0.0, deadline - time.perf_counter())
            qvec = await asyncio.wait_for(asyncio.shield(qvec_task), wait)
        except (asyncio.TimeoutError, Exception):
            qvec = None
    if qvec is not None:
        dense = INDEX.vectors @ qvec
        fused = dense_weight * _norm(dense) + (1 - dense_weight) * _norm(sparse)
        mode = "hybrid"
    else:
        fused, mode = _norm(sparse), "keyword"
    if fused.max() <= 0:
        return []

    # paragraph score = best sentence + a little credit for a second supporting sentence
    by_chunk: dict[int, list[int]] = {}
    for i in np.argsort(-fused):
        by_chunk.setdefault(INDEX.sentences[i].chunk, []).append(int(i))
    ranked = sorted(by_chunk.items(),
                    key=lambda kv: -(fused[kv[1][0]] + 0.25 * (fused[kv[1][1]] if len(kv[1]) > 1 else 0)))
    top_score = fused[ranked[0][1][0]]
    out = []
    for chunk_idx, sent_ids in ranked[:k]:
        score = float(fused[sent_ids[0]])
        if score < 0.8 * top_score:
            break
        best = sorted(sent_ids[:2])  # the paragraph's two best sentences, in document order
        c = INDEX.chunks[chunk_idx]
        out.append({"title": c.title, "file": c.file, "snippet": " ".join(INDEX.sentences[i].text for i in best),
                    "score": round(score, 3), "match": mode})
    return out

"""Vector search over the in-memory Company Brain index (keyword fallback without a key)."""
import numpy as np

from brain.ingest import INDEX, embed, tokens

_query_cache: dict[str, np.ndarray] = {}


async def warm_queries(queries: list[str]):
    """Pre-embed the fast-path queries so their lookups skip the embedding round-trip (~400ms)."""
    if INDEX.vectors is None or not queries:
        return
    for q, v in zip(queries, await embed(queries)):
        _query_cache[q] = v


async def retrieve(query: str, k: int = 3) -> list[dict]:
    if not INDEX.chunks:
        return []
    if INDEX.vectors is not None:
        try:
            q = _query_cache.get(query)
            if q is None:
                q = (await embed([query]))[0]
            return _top(INDEX.vectors @ q, k)
        except Exception as e:
            print(f"[retrieve] embedding query failed, keyword fallback: {e}")
    q_tokens = set(tokens(query))
    scores = np.array([sum(INDEX.idf.get(t, 0) * min(bag[t], 2) for t in q_tokens) for bag in INDEX.bow])
    return _top(scores, k)


def _top(scores: np.ndarray, k: int) -> list[dict]:
    out = []
    for i in np.argsort(-scores)[:k]:
        if scores[i] <= 0:
            continue
        c = INDEX.chunks[i]
        out.append({"title": c.title, "file": c.file, "snippet": c.text, "score": round(float(scores[i]), 3)})
    return out

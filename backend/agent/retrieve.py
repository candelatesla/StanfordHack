"""Vector search over the in-memory Company Brain index (keyword fallback without a key)."""
import numpy as np

from brain.ingest import INDEX, embed, tokens


async def retrieve(query: str, k: int = 3) -> list[dict]:
    if not INDEX.chunks:
        return []
    if INDEX.vectors is not None:
        try:
            q = (await embed([query]))[0]
            scores = INDEX.vectors @ q
            return _top(scores, k)
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

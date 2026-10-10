"""Hybrid retrieval quality: paraphrased / context-heavy questions should still land on the right doc and
quote the right sentence.  python test_retrieval.py   (dense half needs NEBIUS_API_KEY; BM25 always runs)"""
import asyncio
import time

from agent.retrieve import embed_query, retrieve
from brain.ingest import INDEX, build_index

CASES = [
    # (utterance, expected doc title, a phrase the quoted snippet should contain)
    ("Can you do 20% off if we sign for 3 years?", "Discount Policy", "3-year"),
    ("We're a hospital network, so patient records will flow through your platform. Is that a problem?",
     "HIPAA and Business Associate Agreements", "Enterprise tier only"),
    ("Our finance team pays everything about two months after the invoice lands.", "Payment Terms", "Net 60"),
    ("If your platform causes us a breach, how much are you on the hook for?", "Liability and Indemnity", "1x"),
    ("Our data has to stay inside Europe for regulatory reasons.", "Data Residency", "Frankfurt"),
    ("What happens if your service goes down in the middle of the night?", "SLA and Support", "24/7"),
    ("Can our people log in with Okta?", "Product Features", "SSO"),
    ("Our auditors will want to see your security certification.", "SOC 2 and GDPR", "SOC 2 Type II"),
]


async def main():
    await build_index()
    mode = "hybrid" if INDEX.vectors is not None else "BM25-only"
    fails = snippet_misses = 0
    for text, title, phrase in CASES:
        t0 = time.perf_counter()
        q = asyncio.create_task(embed_query(text))
        hits = await retrieve(text, "", q, None)
        ms = (time.perf_counter() - t0) * 1000
        top = hits[0] if hits else {"title": None, "snippet": ""}
        ok = top["title"] == title
        quoted = phrase.lower() in top["snippet"].lower()
        fails += not ok
        snippet_misses += ok and not quoted
        print(f"{('OK  ' if quoted else 'OK* ') if ok else 'FAIL'} {ms:5.0f}ms {text[:70]!r:74} -> {top['title']} | {top['snippet'][:90]!r}")
    print(f"{mode}: right doc {len(CASES) - fails}/{len(CASES)}, key sentence quoted "
          f"{len(CASES) - fails - snippet_misses}/{len(CASES)}  (OK* = right doc, other sentence quoted)")
    print("ALL OK" if not fails else f"{fails} FAILED")


asyncio.run(main())

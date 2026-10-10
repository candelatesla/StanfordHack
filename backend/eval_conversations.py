"""Evaluate on realistic calls (demo/conversations.json): greetings, rapport, small talk and real asks.
    python eval_conversations.py            # gate + speaker(text) + retrieval (hybrid with NEBIUS key)
Reports:
  gate       which customer turns are worth an API call (precision/recall vs. labels, calls saved)
  retrieval  top doc for every turn that needs an answer
  rules      verdict for turns that carry a negotiable ask
"""
import asyncio
import json

import config
from agent import rules
from agent.gate import assess
from agent.retrieve import embed_query, retrieve
from brain.ingest import INDEX, build_index
import crm

DATA = config.ROOT.parent / "demo" / "conversations.json"


async def main():
    await build_index()
    crm.init(reset=True)
    deal = crm.get_deal()
    calls = json.loads(DATA.read_text(encoding="utf-8"))["calls"]
    tp = fp = fn = tn = 0
    doc_ok = doc_n = verdict_ok = verdict_n = 0
    turns_total = 0
    for call in calls:
        print(f"\n=== {call['name']} ===")
        for speaker, text, needs, doc, verdict in call["turns"]:
            turns_total += 1
            g = assess(text)
            if speaker == "customer":
                cand = g["candidate"]
                tp += cand and needs
                fp += cand and not needs
                fn += needs and not cand
                tn += not needs and not cand
                mark = "OK " if cand == needs else ("MISS" if needs else "XTRA")
                print(f"[{mark}] gate={'call' if cand else 'skip'} ({g['why']}, rel {g['relevance']}) | {text[:70]}")
            if needs:
                q = asyncio.create_task(embed_query(text))
                hint = g["items"][0]["query"] if g["items"] else ""
                hits = await retrieve(text, hint, q, None)
                top = hits[0]["title"] if hits else None
                doc_n += 1
                doc_ok += top in doc.split("|")
                if top not in doc.split("|"):
                    print(f"       retrieval: got {top!r}, want {doc!r}")
                if verdict:
                    verdict_n += 1
                    got = rules.check(g["items"][0], deal)["verdict"] if g["items"] else None
                    verdict_ok += got == verdict
                    if got != verdict:
                        print(f"       rules: got {got}, want {verdict} (items={[(i['category'], {k: v for k, v in i['request'].items() if v}) for i in g['items']]})")
    customer_turns = tp + fp + fn + tn
    print("\n=== summary ===")
    print(f"gate:      recall {tp}/{tp + fn} needed turns caught, precision {tp}/{tp + fp}; "
          f"{fn} missed, {fp} unnecessary calls")
    print(f"           API calls on {tp + fp}/{customer_turns} customer turns "
          f"({turns_total} turns in total incl. rep) -> {100 * (1 - (tp + fp) / turns_total):.0f}% of turns cost nothing")
    print(f"retrieval: right doc {doc_ok}/{doc_n} ({'hybrid' if INDEX.vectors is not None else 'BM25 only'})")
    print(f"rules:     right verdict {verdict_ok}/{verdict_n}")


asyncio.run(main())

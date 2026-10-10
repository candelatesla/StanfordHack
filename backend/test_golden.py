"""Golden-demo smoke test: python test_golden.py (works with or without API keys)."""
from fastapi.testclient import TestClient

import config
from main import app

LLM = config.LLM_ENABLED
LINES = [
    ("Can you do 20% off if we sign for 3 years?", "counter", {"discount_pct": 15, "term_years": 3}),
    ("We're in healthcare. Do you sign a HIPAA BAA?", "info", {"hipaa_baa": True}),
    ("We need net-60 payment terms.", "approved", {"payment_terms_days": 60}),
    ("Can you raise the liability cap to 3x fees?", "escalate", {"liability_cap_multiple": 2}),
]


def until(ws, pred, limit=80):
    for _ in range(limit):
        ev = ws.receive_json()
        if pred(ev):
            return ev
    raise AssertionError("event not received")


def done(e):
    return e["type"] == "suggestion" and e["status"] == "done"


def settled(e):
    """Card finished and, with an LLM, its background check has landed too."""
    return done(e) and (e.get("check") is not None or not LLM)


def fresh(ws):
    assert ws.receive_json()["type"] == "init"
    ws.send_json({"type": "reset"})
    until(ws, lambda e: e["type"] == "init")
    return ws


def golden(ws):
    fresh(ws)
    ws.send_json({"type": "text_turn", "speaker": "customer", "text": "Hi, thanks for joining, how's your week?"})
    until(ws, lambda e: e["type"] == "transcript")
    for text, verdict, changes in LINES:
        ws.send_json({"type": "text_turn", "speaker": "customer", "text": text})
        card = until(ws, settled)
        print(f"{card['verdict']:9} {card['latency_ms']:4}ms {card['path']}/{card['check']} | {card['request']}\n"
              f"   ANSWER: {card['answer']}\n   SAY:    {card['counteroffer']}\n"
              f"   SRC:    {[s['title'] for s in card['sources']]}")
        assert card["verdict"] == verdict, card
        assert card["proposed_changes"] == changes, card["proposed_changes"]
        if verdict == "escalate":
            ws.send_json({"type": "escalate", "id": card["id"]})
            print("  ->", until(ws, lambda e: e["type"] == "escalation"))
        else:
            ws.send_json({"type": "accept", "id": card["id"]})
            upd = until(ws, lambda e: e["type"] == "deal_update")
            print("  -> changes:", [(d["clause"], d["old"], d["new"]) for d in upd["changes"]])
    ws.send_json({"type": "end_call"})
    s = until(ws, lambda e: e["type"] == "summary")
    print("SUMMARY recap:", s["recap"], "\nagreed:", s["agreed"], "\nopen:", s["open_items"],
          "\nnext:", s["next_steps"])
    assert upd["fields"]["arr"] == 127500
    print("GOLDEN DEMO OK")


def split_and_dedupe(ws):
    """STT split one ask across two turns -> one card, updated in place; a repeat reuses it too."""
    fresh(ws)
    ws.send_json({"type": "text_turn", "speaker": "customer", "text": "Can you do 20% off"})
    first = until(ws, settled)
    ws.send_json({"type": "text_turn", "speaker": "customer", "text": "if we sign for three years?"})
    second = until(ws, lambda e: settled(e) and "3-year" in e["request"])
    print(f"split: {first['id']} {first['request']!r} -> {second['id']} {second['request']!r} "
          f"({second['path']}/{second['check']})")
    assert first["id"] == second["id"] and second["proposed_changes"] == {"discount_pct": 15, "term_years": 3}
    ws.send_json({"type": "text_turn", "speaker": "customer", "text": "So can you do 20% off for 3 years?"})
    third = until(ws, settled)
    assert third["id"] == first["id"], third["id"]
    print("SPLIT/DEDUPE OK")


with TestClient(app) as client:
    with client.websocket_connect("/ws/events") as ws:
        golden(ws)
    with client.websocket_connect("/ws/events") as ws:
        split_and_dedupe(ws)

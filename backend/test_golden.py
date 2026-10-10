"""Golden-demo smoke test: python test_golden.py (works with or without API keys)."""
from fastapi.testclient import TestClient

from main import app

LINES = [
    ("Can you do 20% off if we sign for 3 years?", "counter", {"discount_pct": 15, "term_years": 3}),
    ("We're in healthcare. Do you sign a HIPAA BAA?", "info", {"hipaa_baa": True}),
    ("We need net-60 payment terms.", "approved", {"payment_terms_days": 60}),
    ("Can you raise the liability cap to 3x fees?", "escalate", {"liability_cap_multiple": 2}),
]


def until(ws, pred, limit=50):
    for _ in range(limit):
        ev = ws.receive_json()
        if pred(ev):
            return ev
    raise AssertionError("event not received")


with TestClient(app) as c, c.websocket_connect("/ws/events") as ws:
    assert ws.receive_json()["type"] == "init"
    ws.send_json({"type": "reset"})
    until(ws, lambda e: e["type"] == "init")
    ws.send_json({"type": "text_turn", "speaker": "customer", "text": "Hi, thanks for joining, how's your week?"})
    until(ws, lambda e: e["type"] == "transcript")
    for text, verdict, changes in LINES:
        ws.send_json({"type": "text_turn", "speaker": "customer", "text": text})
        card = until(ws, lambda e: e["type"] == "suggestion" and e["status"] == "done")
        print(f"{card['verdict']:9} {card['latency_ms']:4}ms | {card['request']}\n   ANSWER: {card['answer']}\n   SAY:    {card['counteroffer']}\n   SRC:    {[s['title'] for s in card['sources']]}")
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
    print("SUMMARY agreed:", s["agreed"], "\nopen:", s["open_items"], "\ncrm:", s["crm_changes"])
    assert upd["fields"]["arr"] == 127500
print("GOLDEN DEMO OK")

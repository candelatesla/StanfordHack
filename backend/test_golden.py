"""End-to-end over the event socket: python test_golden.py (works with or without API keys).
All turns are sent with speaker="auto", like a single mic or the dev input box."""
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


def until(ws, pred, limit=120):
    for _ in range(limit):
        ev = ws.receive_json()
        if pred(ev):
            return ev
    raise AssertionError("event not received")


def settled(e):
    """Card finished and, with an LLM, its background check has landed too."""
    return e["type"] == "suggestion" and e["status"] == "done" and (e.get("check") is not None or not LLM)


def fresh(ws):
    assert ws.receive_json()["type"] == "init"
    ws.send_json({"type": "reset"})
    until(ws, lambda e: e["type"] == "init")


def say(ws, text, speaker="auto"):
    ws.send_json({"type": "text_turn", "speaker": speaker, "text": text})
    return until(ws, lambda e: e["type"] == "transcript" and e["text"] == text)


def golden(ws):
    fresh(ws)
    turn = say(ws, "Hi, thanks for joining, how's your week?")
    print(f"auto speaker: {turn['speaker']:8} | {turn['text']}")
    handled = set()
    for text, verdict, changes in LINES:
        turn = say(ws, text)
        card = until(ws, lambda e: settled(e) and e["id"] not in handled)  # skip late updates of old cards
        handled.add(card["id"])
        print(f"auto speaker: {turn['speaker']:8} | {text}\n"
              f"  {card['verdict']:9} {card['latency_ms']:4}ms {card['path']}/{card['check']} | {card['request']}\n"
              f"   SAY: {card['counteroffer']}\n   SRC: {[(s['title'], s['match']) for s in card['sources']]}")
        assert turn["speaker"] == "customer", turn
        assert card["verdict"] == verdict, card
        assert card["proposed_changes"] == changes, card["proposed_changes"]
        if verdict == "escalate":
            ws.send_json({"type": "escalate", "id": card["id"]})
            until(ws, lambda e: e["type"] == "escalation")
        else:
            ws.send_json({"type": "accept", "id": card["id"]})
            upd = until(ws, lambda e: e["type"] == "deal_update")
        # the rep reads the card aloud -> detected as rep, no new card
        rep = say(ws, card["counteroffer"])
        print(f"auto speaker: {rep['speaker']:8} | {rep['text'][:70]}")
        assert rep["speaker"] == "rep", rep
    ws.send_json({"type": "end_call"})
    s = until(ws, lambda e: e["type"] == "summary")
    print("SUMMARY recap:", s["recap"], "\nopen:", s["open_items"])
    assert upd["fields"]["arr"] == 127500
    assert not any(c.startswith("s5") for c in s["open_items"]), "rep turns must not create cards"
    print("GOLDEN DEMO OK")


def split_and_dedupe(ws):
    """STT split one ask across two turns -> one card, updated in place; a repeat reuses it too."""
    fresh(ws)
    say(ws, "Can you do 20% off")
    first = until(ws, settled)
    say(ws, "if we sign for three years?")
    second = until(ws, lambda e: settled(e) and "3-year" in e["request"])
    print(f"split: {first['id']} {first['request']!r} -> {second['id']} {second['request']!r}")
    assert first["id"] == second["id"] and second["proposed_changes"] == {"discount_pct": 15, "term_years": 3}
    say(ws, "So can you do 20% off for 3 years?")
    third = until(ws, settled)
    assert third["id"] == first["id"], third["id"]
    print("SPLIT/DEDUPE OK")


def multi_intent(ws):
    """One sentence, two asks -> two cards, each with its own source."""
    fresh(ws)
    say(ws, "We'd need net 60, and does that include a HIPAA BAA?")
    cards = {}
    while len(cards) < 2 or not all(settled(c) for c in cards.values()):
        e = until(ws, lambda e: e["type"] == "suggestion")
        cards[e["id"]] = e
    got = sorted((c["category"], c["verdict"], c["sources"][0]["title"]) for c in cards.values())
    print("multi:", got)
    assert [g[0] for g in got] == ["compliance", "payment"], got
    assert got[0][2].startswith("HIPAA") and got[1][2] == "Payment Terms", got
    print("MULTI-INTENT OK")


def manual_flip(ws):
    """Mislabelled turn -> click the speaker label -> card appears (and disappears when flipped back)."""
    fresh(ws)
    turn = say(ws, "Could we get net 90 instead?", speaker="rep")  # e.g. the rep mic picked up the customer
    ws.send_json({"type": "set_speaker", "id": turn["id"], "speaker": "customer"})
    until(ws, lambda e: e["type"] == "transcript_update" and e["speaker"] == "customer")
    card = until(ws, lambda e: e["type"] == "suggestion" and e["status"] == "done")
    print(f"flip -> customer: {card['verdict']} {card['request']} (to {card['approval_needed']})")
    assert card["verdict"] == "escalate" and card["approval_needed"] == "Finance"
    ws.send_json({"type": "set_speaker", "id": turn["id"], "speaker": "rep"})
    until(ws, lambda e: e["type"] == "suggestion_removed" and e["id"] == card["id"])
    print("MANUAL FLIP OK")


def meet_echo(ws):
    """Meet mode without headphones: the rep mic re-hears the customer; only the rep's own words become rep turns."""
    fresh(ws)
    say(ws, "Do you integrate with Snowflake and Salesforce?", speaker="customer")
    ws.send_json({"type": "text_turn", "speaker": "rep", "text": "do you integrate with snowflake and salesforce"})
    stats = until(ws, lambda e: e["type"] == "stats" and e.get("echoes"))
    assert stats["echoes"] == 1, stats
    turn = say(ws, "Yes, both, natively.", speaker="rep")
    assert turn["speaker"] == "rep", turn
    print("MEET ECHO OK")


with TestClient(app) as client:
    for scenario in (golden, split_and_dedupe, multi_intent, manual_flip, meet_echo):
        with client.websocket_connect("/ws/events") as ws:
            scenario(ws)

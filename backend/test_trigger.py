"""Instant extractors (no keys needed): python test_trigger.py
- every ask in a turn, incl. STT-style spelled-out numbers
- who is speaking, from phrasing + context"""
from agent.speaker import CONFIDENT, guess
from agent.trigger import heuristic

ASKS = [
    ("Can you do 20% off if we sign for 3 years?", [("pricing", {"discount_pct": 20, "term_years": 3})]),
    ("could you do twenty percent off if we sign for three years", [("pricing", {"discount_pct": 20, "term_years": 3})]),
    ("What about twenty-five percent on a two-year deal?", [("pricing", {"discount_pct": 25, "term_years": 2})]),
    ("We're in healthcare. Do you sign a HIPAA BAA?", [("compliance", {"feature": "hipaa_baa"})]),
    ("we're in health care do you sign a hipaa b a a", [("compliance", {"feature": "hipaa_baa"})]),
    ("We need net-60 payment terms.", [("payment", {"payment_terms_days": 60})]),
    ("we need net sixty payment terms", [("payment", {"payment_terms_days": 60})]),
    ("Can you raise the liability cap to three times fees", [("legal", {"liability_multiple": 3})]),
    ("Do you have a SOC 2 report?", [("compliance", {"feature": "soc2"})]),
    # several asks in one breath -> one card each, in the order they were said
    ("We'd need net 60, and does that include a HIPAA BAA?",
     [("payment", {"payment_terms_days": 60}), ("compliance", {"feature": "hipaa_baa"})]),
    ("If we do 3 years at 15% off, can you also bump the liability cap to 2x and give us SSO?",
     [("pricing", {"discount_pct": 15, "term_years": 3}), ("legal", {"liability_multiple": 2}),
      ("product", {"feature": "sso"})]),
    ("Thanks for making time. We've reviewed the Enterprise proposal.", []),
    ("Okay, that works for us. Send over the paperwork.", []),
]

# (text, previous speaker, expected, must be confident?)
SPEAKERS = [
    ("Can you do 20% off if we sign for 3 years?", None, "customer", True),
    ("We're in healthcare. Do you sign a HIPAA BAA?", "rep", "customer", True),
    ("We need net-60 payment terms.", "rep", "customer", True),
    ("Can you raise the liability cap to 3x fees?", "rep", "customer", True),
    ("Okay, that works for us. Send over the paperwork.", "rep", "customer", False),
    ("We can do 15% with a 3-year commitment, and that's approved today.", "customer", "rep", True),
    ("Let me check with Legal and get back to you on the cap.", "customer", "rep", True),
    ("Thanks for joining! How many analysts are on your team today?", None, "rep", True),
    ("Yes, the HIPAA BAA is included in your Enterprise plan.", "customer", "rep", True),
]

CARDS = [{"counteroffer": "We can give you 15% off for a three-year commitment, and that's approved today."}]

fails = 0
for text, want in ASKS:
    got = heuristic(text)["items"]
    ok = len(got) == len(want) and all(
        g["category"] == c and all(g["request"].get(k) == v for k, v in f.items()) for g, (c, f) in zip(got, want))
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} asks    {text!r:90} -> {[(g['category'], {k: v for k, v in g['request'].items() if v is not None}) for g in got] if not ok else len(got)}")

for text, prev, want, need_conf in SPEAKERS:
    history = [{"speaker": prev, "text": "..."}] if prev else []
    who, score = guess(text, history, [])
    ok = who == want and (score >= CONFIDENT or not need_conf)
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} speaker {text!r:90} -> {who} ({score:.1f})")

# the rep reading the suggestion card aloud is the strongest rep signal
who, score = guess("Okay so we can give you fifteen percent off for a three year commitment", [], CARDS)
ok = who == "rep" and score >= CONFIDENT
fails += not ok
print(f"{'OK  ' if ok else 'FAIL'} speaker (reads card aloud) -> {who} ({score:.1f})")

print("ALL OK" if not fails else f"{fails} FAILED")

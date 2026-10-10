"""Heuristic extractor on STT-style phrasings: python test_trigger.py"""
from agent.trigger import heuristic

CASES = [
    ("Can you do 20% off if we sign for 3 years?", "pricing", {"discount_pct": 20, "term_years": 3}),
    ("could you do twenty percent off if we sign for three years", "pricing", {"discount_pct": 20, "term_years": 3}),
    ("What about twenty-five percent on a two-year deal?", "pricing", {"discount_pct": 25, "term_years": 2}),
    ("We're in healthcare. Do you sign a HIPAA BAA?", "compliance", {"feature": "hipaa_baa"}),
    ("we're in health care do you sign a hipaa b a a", "compliance", {"feature": "hipaa_baa"}),
    ("We need net-60 payment terms.", "payment", {"payment_terms_days": 60}),
    ("we need net sixty payment terms", "payment", {"payment_terms_days": 60}),
    ("Could we do net ninety?", "payment", {"payment_terms_days": 90}),
    ("Can you raise the liability cap to 3x fees?", "legal", {"liability_multiple": 3}),
    ("can you raise the liability cap to three times fees", "legal", {"liability_multiple": 3}),
    ("can you raise the liability cap to three x fees", "legal", {"liability_multiple": 3}),
    ("Do you have a SOC 2 report?", "compliance", {"feature": "soc2"}),
    ("Do you support SSO?", "product", {"feature": "sso"}),
    ("Thanks for making time. We've reviewed the Enterprise proposal.", None, {}),
    ("Okay, that works for us. Send over the paperwork.", None, {}),
]

fails = 0
for text, cat, fields in CASES:
    out = heuristic(text)
    got_cat = out.get("category") if out["needs_brain"] else None
    ok = got_cat == cat and all(out["request"].get(k) == v for k, v in fields.items())
    fails += not ok
    print(f"{'OK  ' if ok else 'FAIL'} {text!r:75} -> {got_cat} {out.get('request') if not ok else ''}")
print("ALL OK" if not fails else f"{fails} FAILED")

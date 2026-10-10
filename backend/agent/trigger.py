"""Understand a call turn: who said it, and every distinct thing the customer is asking for.

Two extractors produce the same shape, {"speaker", "items": [{category, request, query, pos}]}:
- heuristic(): regex/keyword, ~0ms. Finds *all* asks in a turn (not just the first keyword).
- llm_extract(): FAST_MODEL reads the whole utterance plus recent context; it understands
  follow-ups ("what about three years?"), paraphrases and mixed asks, and labels the speaker.
The pipeline shows fast-path cards immediately and lets the LLM confirm/revise them.
"""
import json
import re

import config
from agent.llm import client, extra

REQUEST_FIELDS = ("discount_pct", "term_years", "payment_terms_days", "feature", "liability_multiple")
NUMERIC = ("discount_pct", "term_years", "payment_terms_days", "liability_multiple")
CATEGORIES = {"pricing", "payment", "compliance", "legal", "product"}
FEATURES = ("hipaa_baa", "eu_residency", "soc2", "gdpr", "sso", "api", "dedicated_csm",
            "termination_for_convenience", "sla", "on_premise")

PROMPT = f"""You monitor a live B2B sales call. Nimbus Analytics' REP is selling; the CUSTOMER is buying.
Read the latest utterance in the context of the conversation and return JSON only:
{{"speaker": "customer" | "rep",
  "requests": [{{"category": "pricing|payment|compliance|legal|product",
                "discount_pct": number|null, "term_years": number|null, "payment_terms_days": number|null,
                "liability_multiple": number|null, "feature": string|null,
                "query": "short knowledge-base search query"}}]}}
Rules:
- speaker: who most likely said the latest utterance. Customers ask for concessions, state requirements,
  raise objections. Reps offer, explain policy, ask discovery questions, or read approved answers aloud.
- requests: EVERY distinct thing the customer is asking for or about that needs company knowledge
  (pricing, discounts, payment terms, compliance, legal terms, product capabilities). Use the whole
  sentence and the context: "what about three years?" right after a 20% discount ask is
  pricing with discount_pct 20 and term_years 3. Small talk, agreement or rep speech -> [].
- Extract numbers literally; never invent values. feature is one of {", ".join(FEATURES)} or a short name."""


def _empty_request() -> dict:
    return {k: None for k in REQUEST_FIELDS}


def _item(category: str, request: dict, query: str, pos: int = 0) -> dict:
    return {"category": category, "request": request, "query": query, "pos": pos}


# ---------------------------------------------------------------- LLM extractor

async def llm_extract(text: str, context: list[dict]) -> dict:
    ctx = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in context[-6:])
    resp = await client.chat.completions.create(
        model=config.FAST_MODEL,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": f"Conversation so far:\n{ctx or '(start of call)'}\n\n"
                                              f"Latest utterance: {text}"}],
        temperature=0,
        max_tokens=800,
        response_format={"type": "json_object"},
        **extra(config.FAST_MODEL),
    )
    return normalise_llm(json.loads(resp.choices[0].message.content), text)


def normalise_llm(out: dict, text: str) -> dict:
    speaker = out.get("speaker") if out.get("speaker") in ("customer", "rep") else None
    items = []
    for i, r in enumerate(out.get("requests") or []):
        if not isinstance(r, dict):
            continue
        req = _empty_request()
        req.update({k: r.get(k) for k in REQUEST_FIELDS if r.get(k) is not None})
        for k in NUMERIC:  # models sometimes return "20%" or "3"
            if isinstance(req[k], str):
                m = re.search(r"\d+(?:\.\d+)?", req[k])
                req[k] = float(m.group()) if m else None
        cat = r.get("category")
        if cat not in CATEGORIES:
            hits = heuristic(text)["items"]
            cat = hits[0]["category"] if hits else "product"
        items.append(_item(cat, req, r.get("query") or text, i))
    return {"speaker": speaker, "items": items}


# ---------------------------------------------------------------- regex extractor

_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "double": 2, "triple": 3}
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SPOKEN = re.compile(r"\b(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(list(_UNITS)[1:10]) + r"))?\b|\b("
                     + "|".join(_UNITS) + r")\b")


def _num(s: str):
    return _WORDNUM.get(s.lower()) or float(s)


def words_to_digits(t: str) -> str:
    """STT often spells numbers out: 'twenty five percent' -> '25 percent', 'net sixty' -> 'net 60'."""
    def sub(m):
        if m.group(1):
            return str(_TENS[m.group(1)] + (_UNITS[m.group(2)] if m.group(2) else 0))
        return str(_UNITS[m.group(3)])
    return _SPOKEN.sub(sub, t)


# Keyword hints appended to the search query (they steer BM25; the utterance itself drives the dense search)
FAST_QUERIES = {
    "discount": "multi-year discount policy approval",
    "pricing": "pricing tiers and discount policy",
    "net": "payment terms net 60 approval",
    "payment": "payment terms",
    "liability": "limitation of liability cap escalation",
    "termination": "termination for convenience",
    "hipaa_baa": "HIPAA business associate agreement BAA",
    "eu_residency": "EU data residency",
    "soc2": "SOC 2 Type II report",
    "gdpr": "GDPR DPA",
    "sso": "SSO features by tier",
    "api": "API access by tier",
    "dedicated_csm": "dedicated customer success manager",
    "sla": "SLA uptime support",
    "on_premise": "on-premise deployment",
}

FEATURE_WORDS = [
    (("hipaa", "baa", "b a a", "phi", "healthcare", "health care"), "hipaa_baa", "compliance"),
    (("residency", "eu data", "data in europe", "frankfurt"), "eu_residency", "compliance"),
    (("soc 2", "soc2", "soc ii", "soc two"), "soc2", "compliance"),
    (("gdpr", "dpa"), "gdpr", "compliance"),
    (("sso", "saml", "single sign"), "sso", "product"),
    (("api",), "api", "product"),
    (("csm", "customer success"), "dedicated_csm", "product"),
    (("sla", "uptime", "24/7", "support hours"), "sla", "product"),
    (("on-prem", "on prem", "self-host"), "on_premise", "product"),
]


def heuristic(text: str) -> dict:
    """Finds every recognisable ask in the turn, ordered by where it appears."""
    t = words_to_digits(text.lower().replace(",", ""))
    n = r"(\d+(?:\.\d+)?|one|two|three|four|five)"
    items = []

    term = None
    if m := re.search(rf"{n}\s*(?:-|\s)?\s*years?", t):
        term = int(_num(m.group(1)))

    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t):
        req = _empty_request() | {"discount_pct": float(m.group(1)), "term_years": term}
        items.append(_item("pricing", req, FAST_QUERIES["discount"], m.start()))
    elif m := re.search(r"discount|cheaper|lower the price|better price|pricing|how much|\bprice\b", t):
        items.append(_item("pricing", _empty_request() | {"term_years": term}, FAST_QUERIES["pricing"], m.start()))

    if m := re.search(r"net[\s-]*(\d+)", t):
        req = _empty_request() | {"payment_terms_days": int(m.group(1))}
        items.append(_item("payment", req, FAST_QUERIES["net"], m.start()))
    elif m := re.search(r"payment terms|pay in|pay within", t):
        items.append(_item("payment", _empty_request(), FAST_QUERIES["payment"], m.start()))

    if m := re.search(r"liability|\bcap\b", t):
        req = _empty_request()
        if mm := re.search(rf"{n}\s*(?:x|times)\b", t) or re.search(r"(double|triple)", t):
            req["liability_multiple"] = _num(mm.group(1))
        items.append(_item("legal", req, FAST_QUERIES["liability"], m.start()))
    if m := re.search(r"terminat|cancel", t):
        req = _empty_request() | {"feature": "termination_for_convenience"}
        items.append(_item("legal", req, FAST_QUERIES["termination"], m.start()))

    for words, feature, cat in FEATURE_WORDS:
        hits = [m.start() for w in words if (m := re.search(rf"\b{re.escape(w)}", t))]
        if hits:
            items.append(_item(cat, _empty_request() | {"feature": feature}, FAST_QUERIES[feature], min(hits)))

    items.sort(key=lambda i: i["pos"])
    return {"speaker": None, "items": items}

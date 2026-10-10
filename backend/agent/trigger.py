"""Small model: does this customer turn need the Company Brain? Extract a structured request."""
import asyncio
import json
import re

import config
from agent.llm import client, extra

SCHEMA = {
    "needs_brain": True,
    "category": "pricing|payment|compliance|legal|product",
    "request": {"discount_pct": None, "term_years": None, "payment_terms_days": None,
                "feature": None, "liability_multiple": None},
    "query": "short search query for the knowledge base",
}

PROMPT = f"""You monitor a live B2B sales call for Nimbus Analytics.
Given the latest CUSTOMER turn and the last few turns of context, decide if the rep
needs company knowledge (pricing, discounts, payment terms, compliance, legal terms,
product capabilities). If not, return {{"needs_brain": false}}.
If yes, return JSON exactly in this schema: {json.dumps(SCHEMA)}
Use null for fields that were not mentioned. Extract numbers literally. Never invent values.
For "feature" use one of: hipaa_baa, eu_residency, soc2, gdpr, sso, api, dedicated_csm, termination_for_convenience, or a short free-text name.
Return only JSON."""

CATEGORIES = {"pricing", "payment", "compliance", "legal", "product"}


async def trigger(text: str, context: list[dict]) -> dict:
    """Tiered extraction. Fast path: the regex extractor (~0ms) when it recognises the ask; the LLM
    double-checks it afterwards via verify(). Long tail: the LLM extracts within TRIGGER_TIMEOUT_S."""
    rule_based = heuristic(text)
    if rule_based["needs_brain"] or client is None:
        return {**rule_based, "path": "fast"}
    try:
        out = await asyncio.wait_for(_llm_trigger(text, context), config.TRIGGER_TIMEOUT_S)
        return {**out, "path": "llm"}
    except asyncio.TimeoutError:
        print(f"[trigger] LLM over {config.TRIGGER_TIMEOUT_S * 1000:.0f}ms budget")
    except Exception as e:
        print(f"[trigger] LLM failed: {e}")
    return {**rule_based, "path": "fast"}


NUMERIC = ("discount_pct", "term_years", "payment_terms_days", "liability_multiple")


async def verify(text: str, context: list[dict], fast: dict) -> dict | None:
    """LLM second opinion on a fast-path extraction. Returns a corrected trigger, or None if it agrees."""
    try:
        out = await _llm_trigger(text, context)
    except Exception as e:
        print(f"[verify] LLM failed: {e}")
        return None
    if not out["needs_brain"]:
        return None  # keep the keyword hit; a false negative is worse on a live call
    if out["category"] != fast["category"]:
        return {**out, "path": "llm"}
    merged = dict(fast["request"])
    for k in NUMERIC:
        v = out["request"].get(k)
        if v is not None and (merged.get(k) is None or float(v) != float(merged[k])):
            merged[k] = v
    if merged == fast["request"]:
        return None
    return {**fast, "request": merged, "path": "llm"}


async def _llm_trigger(text: str, context: list[dict]) -> dict:
    ctx = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in context[-4:])
    resp = await client.chat.completions.create(
        model=config.FAST_MODEL,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": f"Context:\n{ctx}\n\nLatest CUSTOMER turn: {text}"}],
        temperature=0,
        max_tokens=600,
        **extra(config.FAST_MODEL),
        response_format={"type": "json_object"},
    )
    return _normalise(json.loads(resp.choices[0].message.content), text)


def _normalise(out: dict, text: str) -> dict:
    if not out.get("needs_brain"):
        return {"needs_brain": False}
    req = {k: None for k in SCHEMA["request"]}
    req.update({k: v for k, v in (out.get("request") or {}).items() if k in req})
    cat = out.get("category")
    if cat not in CATEGORIES:
        cat = heuristic(text).get("category", "product")
    return {"needs_brain": True, "category": cat, "request": req, "query": out.get("query") or text}


_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "double": 2, "triple": 3}


def _num(s: str):
    s = s.lower()
    return _WORDNUM.get(s) or float(s)


_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SPOKEN = re.compile(r"\b(" + "|".join(_TENS) + r")(?:[\s-](" + "|".join(list(_UNITS)[1:10]) + r"))?\b|\b("
                     + "|".join(_UNITS) + r")\b")


def words_to_digits(t: str) -> str:
    """STT often spells numbers out: 'twenty five percent' -> '25 percent', 'net sixty' -> 'net 60'."""
    def sub(m):
        if m.group(1):
            return str(_TENS[m.group(1)] + (_UNITS[m.group(2)] if m.group(2) else 0))
        return str(_UNITS[m.group(3)])
    return _SPOKEN.sub(sub, t)


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


def heuristic(text: str) -> dict:
    """Keyword/regex extractor. Used without an API key and as a safety net when the LLM fails."""
    t = words_to_digits(text.lower().replace("thirty-", "thirty ").replace(",", ""))
    req = {k: None for k in SCHEMA["request"]}
    n = r"(\d+(?:\.\d+)?|one|two|three|four|five)"

    if m := re.search(rf"{n}\s*(?:-|\s)?\s*years?", t):
        req["term_years"] = int(_num(m.group(1)))

    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t):
        req["discount_pct"] = float(m.group(1))
        return _hit("pricing", req, FAST_QUERIES["discount"])
    if any(w in t for w in ("discount", "cheaper", "lower the price", "better price", "pricing", "how much")):
        return _hit("pricing", req, FAST_QUERIES["pricing"])

    if m := re.search(r"net[\s-]*(\d+)", t):
        req["payment_terms_days"] = int(m.group(1))
        return _hit("payment", req, FAST_QUERIES["net"])
    if "payment terms" in t or "pay in" in t:
        return _hit("payment", req, FAST_QUERIES["payment"])

    if "liability" in t or re.search(r"\bcap\b", t):
        if m := re.search(rf"{n}\s*(?:x|times)\b", t) or re.search(r"(double|triple)", t):
            req["liability_multiple"] = _num(m.group(1))
        return _hit("legal", req, FAST_QUERIES["liability"])
    if "termination" in t or "terminate" in t or "cancel" in t:
        req["feature"] = "termination_for_convenience"
        return _hit("legal", req, FAST_QUERIES["termination"])

    for words, feature, cat, q in [
        (("hipaa", "baa", "phi", "healthcare"), "hipaa_baa", "compliance", FAST_QUERIES["hipaa_baa"]),
        (("residency", "eu data", "data in europe", "frankfurt"), "eu_residency", "compliance", FAST_QUERIES["eu_residency"]),
        (("soc 2", "soc2", "soc ii"), "soc2", "compliance", FAST_QUERIES["soc2"]),
        (("gdpr", "dpa"), "gdpr", "compliance", FAST_QUERIES["gdpr"]),
        (("sso", "saml", "single sign"), "sso", "product", FAST_QUERIES["sso"]),
        (("api",), "api", "product", FAST_QUERIES["api"]),
        (("csm", "customer success"), "dedicated_csm", "product", FAST_QUERIES["dedicated_csm"]),
        (("sla", "uptime", "support"), None, "product", FAST_QUERIES["sla"]),
        (("on-prem", "on prem", "self-host"), "on_premise", "product", FAST_QUERIES["on_premise"]),
    ]:
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words):
            req["feature"] = feature
            return _hit(cat, req, q)
    return {"needs_brain": False}


def _hit(category, req, query):
    return {"needs_brain": True, "category": category, "request": req, "query": query}

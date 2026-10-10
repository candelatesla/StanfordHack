"""Small model: does this customer turn need the Company Brain? Extract a structured request."""
import json
import re

import config
from agent.llm import client

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
    if client is None:
        return heuristic(text)
    ctx = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in context[-4:])
    try:
        resp = await client.chat.completions.create(
            model=config.FAST_MODEL,
            messages=[{"role": "system", "content": PROMPT},
                      {"role": "user", "content": f"Context:\n{ctx}\n\nLatest CUSTOMER turn: {text}"}],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        out = json.loads(resp.choices[0].message.content)
        return _normalise(out, text)
    except Exception as e:
        print(f"[trigger] LLM failed, heuristic fallback: {e}")
        return heuristic(text)


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


def heuristic(text: str) -> dict:
    """Keyword/regex extractor. Used without an API key and as a safety net when the LLM fails."""
    t = text.lower()
    req = {k: None for k in SCHEMA["request"]}
    n = r"(\d+(?:\.\d+)?|one|two|three|four|five)"

    if m := re.search(rf"{n}\s*(?:-|\s)?\s*years?", t):
        req["term_years"] = int(_num(m.group(1)))

    if m := re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t):
        req["discount_pct"] = float(m.group(1))
        return _hit("pricing", req, "multi-year discount policy approval")
    if any(w in t for w in ("discount", "cheaper", "lower the price", "better price", "pricing", "how much")):
        return _hit("pricing", req, "pricing tiers and discount policy")

    if m := re.search(r"net[\s-]*(\d+)", t):
        req["payment_terms_days"] = int(m.group(1))
        return _hit("payment", req, "payment terms net 60 approval")
    if "payment terms" in t or "pay in" in t:
        return _hit("payment", req, "payment terms")

    if "liability" in t or re.search(r"\bcap\b", t):
        if m := re.search(rf"{n}\s*(?:x|times)\b", t) or re.search(r"(double|triple)", t):
            req["liability_multiple"] = _num(m.group(1))
        return _hit("legal", req, "limitation of liability cap escalation")
    if "termination" in t or "terminate" in t or "cancel" in t:
        req["feature"] = "termination_for_convenience"
        return _hit("legal", req, "termination for convenience")

    for words, feature, cat, q in [
        (("hipaa", "baa", "phi", "healthcare"), "hipaa_baa", "compliance", "HIPAA business associate agreement BAA"),
        (("residency", "eu data", "data in europe", "frankfurt"), "eu_residency", "compliance", "EU data residency"),
        (("soc 2", "soc2", "soc ii"), "soc2", "compliance", "SOC 2 Type II report"),
        (("gdpr", "dpa"), "gdpr", "compliance", "GDPR DPA"),
        (("sso", "saml", "single sign"), "sso", "product", "SSO features by tier"),
        (("api",), "api", "product", "API access by tier"),
        (("csm", "customer success"), "dedicated_csm", "product", "dedicated customer success manager"),
        (("sla", "uptime", "support"), None, "product", "SLA uptime support"),
        (("on-prem", "on prem", "self-host"), "on_premise", "product", "on-premise deployment"),
    ]:
        if any(re.search(rf"\b{re.escape(w)}", t) for w in words):
            req["feature"] = feature
            return _hit(cat, req, q)
    return {"needs_brain": False}


def _hit(category, req, query):
    return {"needs_brain": True, "category": category, "request": req, "query": query}

"""Understand a call turn: every distinct thing the customer needs an answer on.

Two extractors produce the same shape, {"speaker", "items": [{category, request, query, pos}]}:
- heuristic(): regex/keyword, ~0ms. Finds *all* asks in a turn (not just the first keyword).
- llm_extract(): FAST_MODEL reads the whole utterance plus recent context; it understands
  follow-ups ("what about three years?"), paraphrases, objections and mixed asks, and labels the speaker.
Only turns that pass the gate (agent/gate.py) ever reach the LLM.
"""
import json
import re

import config
from agent.llm import client, extra

REQUEST_FIELDS = ("discount_pct", "term_years", "payment_terms_days", "feature", "liability_multiple",
                  "pilot_days", "renewal_cap_pct", "start_delay_days", "seats")
NUMERIC = ("discount_pct", "term_years", "payment_terms_days", "liability_multiple",
           "pilot_days", "renewal_cap_pct", "start_delay_days", "seats")
# Negotiable categories have deterministic rules; the rest are knowledge questions answered from the docs.
CATEGORIES = {"pricing", "payment", "compliance", "legal", "product", "pilot", "contract",
              "security", "integration", "implementation", "support", "objection", "procurement", "company"}
CATEGORY_BY_FIELD = {"liability_multiple": "legal", "payment_terms_days": "payment", "discount_pct": "pricing",
                     "pilot_days": "pilot", "renewal_cap_pct": "contract", "start_delay_days": "contract",
                     "seats": "contract"}
FEATURES = ("hipaa_baa", "eu_residency", "soc2", "gdpr", "sso", "api", "dedicated_csm",
            "termination_for_convenience", "sla", "on_premise", "implementation_fee", "free_pilot",
            "renewal_cap", "start_date", "seats")

PROMPT = f"""You monitor a live B2B sales call. Nimbus Analytics' REP is selling a data analytics platform;
the CUSTOMER is buying. Read the latest utterance in the context of the conversation and return JSON only:
{{"speaker": "customer" | "rep",
  "requests": [{{"category": "{'|'.join(sorted(CATEGORIES))}",
                "discount_pct": number|null, "term_years": number|null, "payment_terms_days": number|null,
                "liability_multiple": number|null, "pilot_days": number|null, "renewal_cap_pct": number|null,
                "start_delay_days": number|null, "seats": number|null, "feature": string|null,
                "query": "short knowledge-base search query"}}]}}
Rules:
- speaker: who most likely said the latest utterance. Customers ask for concessions, state requirements,
  raise objections and concerns. Reps offer, explain policy, ask discovery questions, or read approved answers.
- requests: EVERY distinct point where the rep needs company knowledge or a decision: concessions
  (discounts, payment terms, pilots, renewal caps, start dates, fee waivers, seats), compliance, security,
  legal terms, integrations, implementation, support, procurement, product capabilities, and objections
  (price too high, competitor, timing/budget, needing other stakeholders). Use the whole sentence and the
  context: "what about three years?" right after a 20% discount ask is pricing with discount_pct 20 and
  term_years 3. Greetings, small talk, thanks, agreement and anything the rep says -> [].
- Extract numbers literally; never invent values. feature is one of {", ".join(FEATURES)} or a short name."""


def _empty_request() -> dict:
    return {k: None for k in REQUEST_FIELDS}


def _item(category: str, request: dict, query: str, pos: int = 0, topic: str | None = None) -> dict:
    """topic: set for knowledge-topic mentions (vs. concrete asks); the gate wants intent for those."""
    return {"category": category, "request": request, "query": query, "pos": pos, "topic": topic}


# ---------------------------------------------------------------- LLM extractor

async def llm_extract(text: str, context: list[dict]) -> dict:
    ctx = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in context[-6:])
    resp = await client.chat.completions.create(
        model=config.FAST_MODEL,
        messages=[{"role": "system", "content": PROMPT},
                  {"role": "user", "content": f"Conversation so far:\n{ctx or '(start of call)'}\n\n"
                                              f"Latest utterance: {text}"}],
        temperature=0,
        max_tokens=900,
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
        # the extracted numbers say what kind of ask it is, whatever label the model picked
        cat = next((c for f, c in CATEGORY_BY_FIELD.items() if req.get(f) is not None), cat)
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
    "sla": "SLA uptime support service credits",
    "on_premise": "on-premise deployment roadmap",
    "pilot": "paid pilot trial credited toward contract",
    "implementation_fee": "implementation fee waived term",
    "implementation": "implementation onboarding timeline training",
    "renewal_cap": "renewal price increase cap",
    "start_date": "start date after signature",
    "seats": "creator seats viewers included blocks",
    "security": "security encryption penetration test questionnaire",
    "integration": "integrations connectors Snowflake Salesforce",
    "data": "data ownership retention deletion export",
    "ai": "AI features data training",
    "objection_price": "price too expensive ROI value",
    "objection_competitor": "competitor comparison cheaper tool",
    "objection_timing": "timing budget next quarter delay",
    "objection_stakeholders": "stakeholders sign-off IT security legal CFO",
    "references": "customer references case studies",
    "roadmap": "roadmap feature request planned",
    "procurement": "procurement vendor forms insurance purchase order",
    "company": "company overview customers employees",
}

FEATURE_WORDS = [  # (words, feature, category, query key)
    (("hipaa", "baa", "b a a", "phi", "healthcare", "health care", "patient data"), "hipaa_baa", "compliance", "hipaa_baa"),
    (("residency", "eu data", "data in europe", "frankfurt", "stay in europe"), "eu_residency", "compliance", "eu_residency"),
    (("soc 2", "soc2", "soc ii", "soc two"), "soc2", "compliance", "soc2"),
    (("gdpr", "dpa"), "gdpr", "compliance", "gdpr"),
    (("sso", "saml", "single sign", "okta", "azure ad", "scim"), "sso", "product", "sso"),
    (("api",), "api", "product", "api"),
    (("csm", "customer success"), "dedicated_csm", "product", "dedicated_csm"),
    (("sla", "uptime", "24/7", "support hours", "outage", "downtime", "goes down"), "sla", "support", "sla"),
    (("on-prem", "on prem", "self-host"), "on_premise", "product", "on_premise"),
]

TOPIC_WORDS = [  # knowledge questions (no rule decides them; answered from the docs)
    (("encrypt", "pen test", "penetration", "security questionnaire", "breach", "mfa", "bug bounty",
      "sub-processor", "subprocessor", "security review", "infosec"), "security", "security"),
    (("snowflake", "bigquery", "redshift", "databricks", "salesforce", "hubspot", "netsuite", "dbt",
      "integrate", "integration", "connector", "connect to", "slack", "teams"), "integration", "integration"),
    (("onboarding", "implementation", "go live", "go-live", "how long does it take", "ramp up", "training"),
     "implementation", "implementation"),
    (("our data", "data ownership", "retention", "delete our data", "deletion", "export our", "who owns"),
     "security", "data"),
    (("ai ", "a.i.", "llm", "chatgpt", "train on our data", "train your model", "nimbus assist"), "product", "ai"),
    (("too expensive", "expensive", "pricey", "out of our budget", "costs too much", "over budget",
      "can't justify"), "objection", "objection_price"),
    (("competitor", "cheaper option", "other vendor", "looker", "tableau", "power bi", "quote from"),
     "objection", "objection_competitor"),
    (("next quarter", "no budget", "not right now", "not the right time", "think about it", "revisit",
      "budget freeze"), "objection", "objection_timing"),
    (("sign off", "sign-off", "run it by", "check with our", "our cfo", "our legal team", "our it team",
      "procurement team", "decision maker"), "objection", "objection_stakeholders"),
    (("reference", "case study", "case studies", "other customers like"), "company", "references"),
    (("roadmap", "on the roadmap", "planning to build", "feature request", "coming soon"), "product", "roadmap"),
    (("w-9", "w9", "vendor form", "purchase order", "insurance", "credit card", "our paper", "vendor registration"),
     "procurement", "procurement"),
    (("how big", "how many customers", "how many employees", "founded", "headquartered", "where are you based"),
     "company", "company"),
]


def heuristic(text: str) -> dict:
    """Finds every recognisable ask in the turn, ordered by where it appears."""
    t = words_to_digits(text.lower().replace(",", ""))
    n = r"(\d+(?:\.\d+)?|one|two|three|four|five)"
    items = []

    term = None
    if m := re.search(rf"{n}\s*(?:-|\s)?\s*years?", t):
        term = int(_num(m.group(1)))

    # percentages mean different things depending on the words around them
    discount_seen = False
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", t):
        window = t[max(0, m.start() - 40):m.end() + 25]
        if re.search(r"uptime|sla|available|availability", window):
            continue
        if re.search(r"renewal|renew|increase|uplift|price lock|raise the price", window):
            req = _empty_request() | {"renewal_cap_pct": float(m.group(1)), "feature": "renewal_cap"}
            items.append(_item("contract", req, FAST_QUERIES["renewal_cap"], m.start()))
        elif not discount_seen:
            discount_seen = True
            req = _empty_request() | {"discount_pct": float(m.group(1)), "term_years": term}
            items.append(_item("pricing", req, FAST_QUERIES["discount"], m.start()))
    if not discount_seen and (m := re.search(r"discount|cheaper|lower the price|better price|\bprice\b|how much", t)):
        items.append(_item("pricing", _empty_request() | {"term_years": term}, FAST_QUERIES["pricing"], m.start()))
    if not any(i["category"] == "contract" for i in items) and \
            (m := re.search(r"renewal (cap|increase|price)|price (lock|increase)s?\b|cap (the )?(renewal|increase)", t)):
        items.append(_item("contract", _empty_request() | {"feature": "renewal_cap"}, FAST_QUERIES["renewal_cap"], m.start()))

    if m := re.search(r"net[\s-]*(\d+)", t):
        req = _empty_request() | {"payment_terms_days": int(m.group(1))}
        items.append(_item("payment", req, FAST_QUERIES["net"], m.start()))
    elif m := re.search(r"payment terms|pay in|pay within|quarterly billing|pay quarterly", t):
        items.append(_item("payment", _empty_request(), FAST_QUERIES["payment"], m.start()))

    if m := re.search(r"liability|\bcap\b(?! (the )?(renewal|increase))", t):
        if not re.search(r"renewal|increase", t[max(0, m.start() - 20):m.end() + 20]):
            req = _empty_request()
            if mm := re.search(rf"{n}\s*(?:x|times)\b", t) or re.search(r"(double|triple)", t):
                req["liability_multiple"] = _num(mm.group(1))
            items.append(_item("legal", req, FAST_QUERIES["liability"], m.start()))
    if m := re.search(r"terminat|cancel (the|our|any)|out clause|for convenience", t):
        req = _empty_request() | {"feature": "termination_for_convenience"}
        items.append(_item("legal", req, FAST_QUERIES["termination"], m.start()))

    if m := re.search(r"pilot|trial|proof of concept|\bpoc\b", t):
        req = _empty_request()
        if mm := re.search(r"(\d+)[\s-]*days?", t):
            req["pilot_days"] = int(mm.group(1))
        elif mm := re.search(r"(\d+)[\s-]*weeks?", t):
            req["pilot_days"] = int(mm.group(1)) * 7
        if re.search(r"free (pilot|trial)|(pilot|trial) for free|no[- ]cost", t):
            req["feature"] = "free_pilot"
        items.append(_item("pilot", req, FAST_QUERIES["pilot"], m.start()))
    if m := re.search(r"implementation fee|onboarding fee|setup fee|set-up fee|waive", t):
        req = _empty_request() | {"feature": "implementation_fee", "term_years": term}
        items.append(_item("contract", req, FAST_QUERIES["implementation_fee"], m.start()))
    if m := re.search(r"start (date|in|on|later)|delay(ed)? (the )?start|push (the )?start|start until", t):
        req = _empty_request() | {"feature": "start_date"}
        if mm := re.search(r"(\d+)[\s-]*days?", t):
            req["start_delay_days"] = int(mm.group(1))
        elif mm := re.search(r"(\d+)[\s-]*months?", t):
            req["start_delay_days"] = int(mm.group(1)) * 30
        items.append(_item("contract", req, FAST_QUERIES["start_date"], m.start()))
    if m := re.search(r"(\d+)\s*(creators?|seats?|licen[cs]es?|users?|analysts?|people (building|who build|creating))", t):
        if re.search(r"need|want|have|add|buy|get|for|about|around", t):
            req = _empty_request() | {"seats": int(m.group(1)), "feature": "seats"}
            items.append(_item("contract", req, FAST_QUERIES["seats"], m.start()))

    for words, feature, cat, qkey in FEATURE_WORDS:
        hits = [m.start() for w in words if (m := re.search(rf"\b{re.escape(w)}", t))]
        if hits:
            items.append(_item(cat, _empty_request() | {"feature": feature}, FAST_QUERIES[qkey], min(hits)))
    covered = {i["category"] for i in items}
    for words, cat, qkey in TOPIC_WORDS:
        hits = [m.start() for w in words if (m := re.search(rf"\b{re.escape(w)}", t))]
        if hits and not (cat in covered and cat != "objection"):
            items.append(_item(cat, _empty_request() | {"feature": None}, FAST_QUERIES[qkey], min(hits), topic=qkey))
            covered.add(cat)

    items.sort(key=lambda i: i["pos"])
    return {"speaker": None, "items": items}

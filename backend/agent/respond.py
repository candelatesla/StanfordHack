"""Larger model: turn verdict + sources into a rep-facing answer and a line to say out loud (streamed)."""
import json
from typing import AsyncIterator

import config
from agent.llm import client, extra

PROMPT = """You are a sales copilot for Nimbus Analytics. Write for the REP, not the customer.
You are given: the customer request, the rules verdict (AUTHORITATIVE, never override it), the current deal
(already-agreed terms; e.g. don't suggest upgrading to a tier they're already on) and source snippets.
Output exactly two lines, nothing else:
ANSWER: <one sentence: what is and isn't allowed, citing approvals if needed>
SAY: <one natural sentence the rep can say out loud to the customer>
Never offer anything beyond the verdict's "allowed" values. Only state what the verdict and sources say;
don't invent restrictions. Never mention field names, rule IDs or file names; use plain sales language
(e.g. "Net 60", "15% for a 3-year term"). Keep each line under 25 words. If the verdict is escalate, SAY should
offer the allowed fallback (if any) and promise to get an answer from the approver quickly.
If no rule covers the request, say it is not in the Company Brain and should be flagged for follow-up."""


async def respond(customer_text: str, trig: dict, verdict: dict, sources: list[dict],
                  deal: dict | None = None) -> AsyncIterator[dict]:
    """Yields progressively more complete {"answer", "counteroffer"} dicts."""
    if client is None:
        yield template(trig, verdict, sources)
        return
    payload = {
        "customer_said": customer_text,
        "request": trig.get("request"),
        "verdict": verdict,
        "current_deal": {k: (deal or {}).get(k) for k in ("customer", "tier", "arr", "discount_pct", "term_years",
                                                          "payment_terms", "liability_cap_multiple", "hipaa_baa")},
        "sources": [{"title": s["title"], "snippet": s["snippet"]} for s in sources[:2]],
    }
    try:
        stream = await client.chat.completions.create(
            model=config.SMART_MODEL,
            messages=[{"role": "system", "content": PROMPT},
                      {"role": "user", "content": json.dumps(payload)}],
            temperature=0.2,
            max_tokens=600,
            **extra(config.SMART_MODEL),
            stream=True,
        )
        buf = ""
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ""
            if not delta:
                continue
            buf += delta
            yield parse(buf)
        final = parse(buf)
        if not final["answer"]:
            yield template(trig, verdict, sources)
    except Exception as e:
        print(f"[respond] LLM failed, template fallback: {e}")
        yield template(trig, verdict, sources)


def parse(buf: str) -> dict:
    answer, say = "", ""
    for line in buf.splitlines():
        s = line.strip().lstrip("-* ").strip()
        up = s.upper()
        if up.startswith("ANSWER:"):
            answer = s[7:].strip()
        elif up.startswith("SAY:"):
            say = s[4:].strip().strip('"')
    if not answer and not say and buf and ":" not in buf[:10]:
        answer = buf.strip()
    return {"answer": answer, "counteroffer": say}


def template(trig: dict, verdict: dict, sources: list[dict]) -> dict:
    """Deterministic card text built straight from the rules verdict."""
    v, allowed, reason = verdict["verdict"], verdict.get("allowed") or {}, verdict.get("reason", "")
    approver = verdict.get("approval_needed")
    if verdict.get("rule_id") is None:
        if sources:
            return {"answer": f"No rule covers this. Closest source: {sources[0]['title']}.",
                    "counteroffer": "Great question. Let me confirm the specifics and follow up in writing today."}
        return {"answer": "Not in Company Brain. Flag for follow-up.",
                "counteroffer": "Let me confirm that with the team and get back to you today."}
    say = ""
    if "discount_pct" in allowed and v != "approved":
        say = f"We can do {allowed['discount_pct']:g}% with a {allowed['term_years']}-year commitment, and that's approved today."
    elif "discount_pct" in allowed:
        say = f"Yes, {allowed['discount_pct']:g}% on a {allowed['term_years']}-year term works for us."
    elif "payment_terms_days" in allowed:
        d = allowed["payment_terms_days"]
        say = f"Yes, we can do Net {d}." if v == "approved" else f"We can offer Net {d} today; anything longer needs Finance sign-off."
    elif "liability_cap_multiple" in allowed:
        m = allowed["liability_cap_multiple"]
        say = (f"I can do {m:g}x fees today; for anything higher I'll get our Legal team on it right away."
               if v == "escalate" else f"Yes, we can raise the cap to {m:g}x fees.")
    elif "tier" in allowed:
        say = f"That's included in our {allowed['tier']} plan; let me show you what upgrading looks like."
    elif v == "info" and allowed:
        say = "Yes, that's included in your Enterprise plan."
    elif v == "escalate":
        say = f"Let me get {approver or 'the team'} on that and come back to you quickly."
    else:
        say = reason + "."
    answer = reason + (f" (needs {approver})" if approver and v != "approved" and approver not in reason else "") + "."
    return {"answer": answer, "counteroffer": say}

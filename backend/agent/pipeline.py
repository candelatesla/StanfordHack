"""Orchestrates: trigger -> (retrieve || rules) -> respond (streamed) -> suggestion events."""
import asyncio
import time

from agent import rules
from agent.respond import respond
from agent.retrieve import retrieve
from agent.trigger import trigger
from session import Session


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def describe_request(trig: dict) -> str:
    r = trig.get("request") or {}
    parts = []
    if r.get("discount_pct") is not None:
        parts.append(f"{r['discount_pct']:g}% discount")
    if r.get("term_years"):
        parts.append(f"{r['term_years']}-year term")
    if r.get("payment_terms_days"):
        parts.append(f"Net {r['payment_terms_days']}")
    if r.get("liability_multiple") is not None:
        parts.append(f"liability cap {r['liability_multiple']:g}x fees")
    if r.get("feature"):
        parts.append(rules.FEATURE_LABELS.get(r["feature"], str(r["feature"]).replace("_", " ")))
    return ", ".join(parts) or trig.get("query", "")


def proposed_changes(verdict: dict) -> dict:
    allowed = dict(verdict.get("allowed") or {})
    for k in ("hipaa_baa", "eu_residency"):
        if k in allowed:
            allowed[k] = bool(allowed[k])
    return {k: v for k, v in allowed.items()
            if k in {"discount_pct", "term_years", "payment_terms_days", "liability_cap_multiple",
                     "hipaa_baa", "eu_residency", "tier"}}


async def on_final_customer_turn(session: Session, text: str):
    t0 = time.perf_counter()
    context = session.transcript[-6:-1]
    trig = await trigger(text, context)
    t_trigger = _ms(t0)
    if not trig.get("needs_brain"):
        print(f"[pipeline] no brain needed ({t_trigger}ms): {text!r}")
        return

    deal = session.deal
    t1 = time.perf_counter()
    sources, verdict = await asyncio.gather(
        retrieve(trig["query"]),
        asyncio.to_thread(rules.check, trig, deal),
    )
    t_lookup = _ms(t1)
    brain_ms = _ms(t0)
    print(f"[pipeline] trigger={t_trigger}ms retrieve+rules={t_lookup}ms brain_total={brain_ms}ms verdict={verdict['verdict']}")

    sid = session.next_id()
    card = {
        "type": "suggestion", "id": sid, "status": "streaming",
        "verdict": verdict["verdict"],
        "category": trig["category"],
        "request": describe_request(trig),
        "customer_text": text,
        "answer": "", "counteroffer": "",
        "sources": [{"title": s["title"], "snippet": s["snippet"]} for s in sources[:2]],
        "proposed_changes": proposed_changes(verdict),
        "approval_needed": verdict.get("approval_needed"),
        "reason": verdict.get("reason"),
        "latency_ms": brain_ms,
        "timing": {"trigger_ms": t_trigger, "lookup_ms": t_lookup},
    }
    session.suggestions[sid] = card
    await session.emit(card)

    first_token_ms = None
    async for partial in respond(text, trig, verdict, sources):
        if first_token_ms is None:
            first_token_ms = _ms(t0)
        card.update(partial)
        await session.emit(card)
    card["status"] = "done"
    card["timing"]["first_token_ms"] = first_token_ms
    card["timing"]["total_ms"] = _ms(t0)
    await session.emit(card)

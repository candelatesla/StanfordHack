"""Orchestrates: trigger -> (retrieve || rules) -> respond (streamed) -> suggestion events."""
import asyncio
import time

from agent import rules
from agent.respond import respond
from agent.retrieve import retrieve
from agent.llm import client
from agent.trigger import trigger, verify
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


MERGE_WINDOW_S = 3.0   # STT can split one ask across two committed turns
DEDUPE_WINDOW_S = 30.0  # don't stack identical cards if the customer repeats themselves


def _find_reusable_card(session: Session, trig: dict, request: str, follow_up_of: str | None) -> str | None:
    """Update an open card instead of stacking a new one: a split/clarified ask, or a repeated one."""
    if follow_up_of and (card := session.suggestions.get(follow_up_of)) and not card.get("resolution") \
            and card["category"] == trig["category"]:
        return follow_up_of
    now = time.time()
    for sid, card in reversed(session.suggestions.items()):
        if not card.get("resolution") and now - card["created"] < DEDUPE_WINDOW_S and card["request"] == request:
            return sid
    return None


async def on_final_customer_turn(session: Session, text: str):
    t0 = time.perf_counter()
    prev = session.last_customer_turn
    recent = prev if prev and time.time() - prev["ts"] < MERGE_WINDOW_S else None
    session.last_customer_turn = turn = {"text": text, "ts": time.time(), "card": None}
    context = session.transcript[-6:-1]
    # Speculative search on the raw words, overlapping the trigger; used only on the LLM path.
    speculative = asyncio.create_task(retrieve(text))
    trigger_text = text
    trig = await trigger(text, context)
    if not trig.get("needs_brain") and recent:
        # a fragment like "...if we sign for 3 years" only makes sense joined to the previous turn
        trigger_text = f"{recent['text']} {text}"
        trig = await trigger(trigger_text, context[:-1])
    t_trigger = _ms(t0)
    if not trig.get("needs_brain"):
        speculative.cancel()
        print(f"[pipeline] no brain needed ({t_trigger}ms): {text!r}")
        return
    turn["text"] = trigger_text

    if trig["path"] == "llm" and trigger_text == text:
        sources_task = speculative
    else:
        speculative.cancel()
        sources_task = retrieve(trig["query"])  # fast-path queries are pre-embedded
    check = asyncio.create_task(verify(trigger_text, context, trig)) if trig["path"] == "fast" and client else None

    sid = _find_reusable_card(session, trig, describe_request(trig), recent and recent["card"]) or session.next_id()
    turn["card"] = sid
    card = await _build_card(session, sid, trig, trigger_text, t0, t_trigger, sources_task,
                             check="llm" if trig["path"] == "llm" or not client else None)

    if check:
        corrected = await check
        if card.get("resolution"):
            return
        if corrected:
            print(f"[pipeline] LLM revised fast-path extraction: {trig['request']} -> {corrected['request']}")
            card = await _build_card(session, sid, corrected, trigger_text, time.perf_counter(), None,
                                     retrieve(corrected["query"]))
            card["check"] = "revised"
        else:
            card["check"] = "verified"
        await session.emit(card)


async def _build_card(session: Session, sid: str, trig: dict, text: str, t0: float, t_trigger: int | None,
                      sources_task, check: str | None = None) -> dict:
    t1 = time.perf_counter()
    deal = session.deal
    sources, verdict = await asyncio.gather(sources_task, asyncio.to_thread(rules.check, trig, deal))
    sources = [s for s in sources if s["score"] >= 0.85 * sources[0]["score"]][:2] if sources else []
    t_lookup = _ms(t1)
    brain_ms = _ms(t0)
    print(f"[pipeline] {trig['path']} path: trigger={t_trigger}ms retrieve+rules={t_lookup}ms "
          f"brain_total={brain_ms}ms verdict={verdict['verdict']}")
    old = session.suggestions.get(sid, {})
    card = {
        "type": "suggestion", "id": sid, "status": "streaming", "created": old.get("created", time.time()),
        "verdict": verdict["verdict"],
        "category": trig["category"],
        "request": describe_request(trig),
        "customer_text": text,
        "answer": "", "counteroffer": "",
        "sources": [{"title": s["title"], "snippet": s["snippet"]} for s in sources],
        "proposed_changes": proposed_changes(verdict),
        "approval_needed": verdict.get("approval_needed"),
        "reason": verdict.get("reason"),
        "latency_ms": old.get("latency_ms", brain_ms),
        "path": trig["path"],
        "check": check,
        "timing": {"trigger_ms": t_trigger, "lookup_ms": t_lookup},
    }
    session.suggestions[sid] = card
    await session.emit(card)

    first_token_ms = None
    async for partial in respond(text, trig, verdict, sources, deal):
        if first_token_ms is None:
            first_token_ms = _ms(t0)
        card.update(partial)
        await session.emit(card)
    card["status"] = "done"
    card["timing"]["first_token_ms"] = first_token_ms
    card["timing"]["total_ms"] = _ms(t0)
    await session.emit(card)
    return card

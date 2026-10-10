"""Orchestrates one transcript turn:

  speaker   source label, or instant phrasing-based guess (+ LLM when unsure)
  requests  regex fast path for every recognised ask; LLM for anything it misses
  per ask   hybrid retrieve (utterance embedding + BM25) || deterministic rules -> respond (streamed)
  verify    LLM reads the whole sentence in context; confirms, revises, adds or removes cards
"""
import asyncio
import time

import config
from agent import rules
from agent.llm import client
from agent.respond import respond
from agent.retrieve import embed_query, retrieve
from agent.trigger import NUMERIC, heuristic, llm_extract
from session import Session

MERGE_WINDOW_S = 3.0     # STT can split one ask across two committed turns
DEDUPE_WINDOW_S = 30.0   # don't stack identical cards if the customer repeats themselves


def _ms(t0: float) -> int:
    return round((time.perf_counter() - t0) * 1000)


def describe_request(item: dict) -> str:
    r = item.get("request") or {}
    parts = []
    if r.get("discount_pct") is not None:
        parts.append(f"{r['discount_pct']:g}% discount")
    if r.get("term_years"):
        parts.append(f"{r['term_years']:g}-year term")
    if r.get("payment_terms_days"):
        parts.append(f"Net {r['payment_terms_days']:g}")
    if r.get("liability_multiple") is not None:
        parts.append(f"liability cap {r['liability_multiple']:g}x fees")
    if r.get("feature"):
        parts.append(rules.FEATURE_LABELS.get(r["feature"], str(r["feature"]).replace("_", " ")))
    return ", ".join(parts) or item.get("query", "")


def proposed_changes(verdict: dict) -> dict:
    allowed = dict(verdict.get("allowed") or {})
    for k in ("hipaa_baa", "eu_residency"):
        if k in allowed:
            allowed[k] = bool(allowed[k])
    return {k: v for k, v in allowed.items()
            if k in {"discount_pct", "term_years", "payment_terms_days", "liability_cap_multiple",
                     "hipaa_baa", "eu_residency", "tier"}}


async def _await(task: asyncio.Task | None, timeout: float):
    if task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        print(f"[pipeline] LLM over {timeout * 1000:.0f}ms budget")
    except Exception as e:
        print(f"[pipeline] LLM failed: {e!r}")
    return None


async def process_turn(session: Session, turn: dict):
    t0 = time.perf_counter()
    text = turn["text"]
    context = [t for t in session.transcript if t["id"] != turn["id"]][-6:]
    maybe_customer = turn["speaker"] == "customer" or (turn["auto"] and not turn["confident"])
    if not maybe_customer:
        return
    llm_task = asyncio.create_task(llm_extract(text, context)) if client else None
    qvec_task = asyncio.create_task(embed_query(text))  # speculative: overlaps speaker/request detection

    # 1. who is speaking (only unsure auto-detected turns wait on the LLM)
    if turn["auto"] and not turn["confident"] and llm_task:
        llm = await _await(llm_task, config.TRIGGER_TIMEOUT_S)
        if llm and llm["speaker"]:
            await session.set_speaker(turn, llm["speaker"])
    if turn["speaker"] != "customer":
        return

    # 2. what are they asking for
    prev = session.last_customer_turn
    recent = prev if prev and time.time() - prev["ts"] < MERGE_WINDOW_S else None
    record = session.last_customer_turn = {"text": text, "ts": time.time(), "cards": []}
    items, path, ask_text = heuristic(text)["items"], "fast", text
    if not items and recent:
        # a fragment like "...if we sign for 3 years" only makes sense joined to the previous turn
        merged = f"{recent['text']} {text}"
        if items := heuristic(merged)["items"]:
            ask_text = merged
    if not items and llm_task:
        llm = await _await(llm_task, config.TRIGGER_TIMEOUT_S)
        items, path = (llm["items"] if llm else []), "llm"
    if not items:
        print(f"[pipeline] no brain needed ({_ms(t0)}ms): {text!r}")
        return
    record["text"] = ask_text
    if ask_text != text:
        qvec_task = asyncio.create_task(embed_query(ask_text))
    t_trigger = _ms(t0)
    follow_up = recent["cards"] if recent else []
    dense_weight = 0.8 if len(items) == 1 else 0.4  # several asks: lean on each ask's own keywords

    async def card_for(item):
        sid = _reusable_card(session, item, follow_up) or session.next_id()
        # Don't hold the card for the embedding (~300-400ms): use whatever has landed right now
        # (BM25 + the extractor's keywords), and upgrade the sources when the vector arrives.
        card = await _build_card(session, sid, item, ask_text, turn["id"], path, t0, t_trigger,
                                 retrieve(ask_text, item["query"], qvec_task, time.perf_counter(),
                                          dense_weight=dense_weight),
                                 check="llm" if path == "llm" or not client else None)
        if card["sources"] and card["sources"][0]["match"] != "hybrid":
            asyncio.create_task(_upgrade_sources(session, card, ask_text, qvec_task, dense_weight))
        return card

    cards = await asyncio.gather(*(card_for(i) for i in items))
    record["cards"] = [c["id"] for c in cards]

    # 3. LLM second opinion on fast-path cards
    if path == "fast" and llm_task:
        llm = await _await(llm_task, 20)
        await _reconcile(session, turn, cards, llm, ask_text, qvec_task)


async def _upgrade_sources(session: Session, card: dict, text: str, qvec_task, dense_weight: float):
    try:
        await qvec_task
    except Exception:
        return
    sources = await retrieve(text, card["item"]["query"], qvec_task, None, dense_weight=dense_weight)
    if sources and session.suggestions.get(card["id"]) is card:
        card["sources"] = [{"title": s["title"], "snippet": s["snippet"], "match": s["match"]} for s in sources]
        await session.emit(card)


def _reusable_card(session: Session, item: dict, follow_up: list[str]) -> str | None:
    """Update an open card instead of stacking a new one: a split/clarified ask, or a repeated one."""
    for sid in follow_up:
        card = session.suggestions.get(sid)
        if card and not card.get("resolution") and card["category"] == item["category"]:
            return sid
    request, now = describe_request(item), time.time()
    for sid, card in reversed(session.suggestions.items()):
        if not card.get("resolution") and now - card["created"] < DEDUPE_WINDOW_S and card["request"] == request:
            return sid
    return None


async def _reconcile(session: Session, turn: dict, cards: list[dict], llm: dict | None, text: str, qvec_task):
    if llm is None:
        for c in cards:
            c["check"] = "rules"
            await session.emit(c)
        return
    if turn["auto"] and not turn["confident"] and llm["speaker"] == "rep":
        await session.set_speaker(turn, "rep")
        for c in cards:
            if not c.get("resolution"):
                await session.remove_card(c["id"])
        return
    unmatched = list(cards)
    for item in llm["items"]:
        feat = item["request"].get("feature")
        if feat and any(c["item"]["request"].get("feature") == feat for c in cards if c not in unmatched):
            continue  # same ask listed twice
        card = next((c for c in unmatched if feat and c["item"]["request"].get("feature") == feat), None)             or next((c for c in unmatched if c["category"] == item["category"]), None)
        if card is None:  # the LLM found an ask the keywords missed
            if item["category"] in {c["category"] for c in cards} and not feat:
                continue
            t0 = time.perf_counter()
            await _build_card(session, session.next_id(), item, text, turn["id"], "llm", t0, None,
                              retrieve(text, item["query"], qvec_task, t0 + 0.5), check="llm")
            continue
        unmatched.remove(card)
        if card.get("resolution"):
            continue
        merged, changed = dict(card["item"]["request"]), False
        for k in NUMERIC:
            v = item["request"].get(k)
            if v is not None and (merged.get(k) is None or float(v) != float(merged[k])):
                merged[k], changed = v, True
        if changed:
            print(f"[pipeline] LLM revised {card['id']}: {card['item']['request']} -> {merged}")
            t0 = time.perf_counter()
            await _build_card(session, card["id"], {**card["item"], "request": merged}, text, turn["id"], "llm",
                              t0, None, retrieve(text, card["item"]["query"], qvec_task, t0 + 0.5),
                              check="revised")
        else:
            card["check"] = "verified"
            await session.emit(card)
    for card in unmatched:  # keyword hit the LLM didn't list: keep it (a missed ask is worse than an extra card)
        card["check"] = "kept"
        await session.emit(card)


async def _build_card(session: Session, sid: str, item: dict, text: str, turn_id: str, path: str,
                      t0: float, t_trigger: int | None, sources_coro, check: str | None = None) -> dict:
    t1 = time.perf_counter()
    deal = session.deal
    sources, verdict = await asyncio.gather(sources_coro, asyncio.to_thread(rules.check, item, deal))
    t_lookup = _ms(t1)
    brain_ms = _ms(t0)
    print(f"[pipeline] {path} path: trigger={t_trigger}ms retrieve+rules={t_lookup}ms "
          f"brain_total={brain_ms}ms verdict={verdict['verdict']} sources={[s['title'] for s in sources]}")
    old = session.suggestions.get(sid, {})
    card = {
        "type": "suggestion", "id": sid, "turn_id": turn_id, "status": "streaming",
        "created": old.get("created", time.time()),
        "verdict": verdict["verdict"],
        "category": item["category"],
        "item": item,
        "request": describe_request(item),
        "customer_text": text,
        "answer": "", "counteroffer": "",
        "sources": [{"title": s["title"], "snippet": s["snippet"], "match": s["match"]} for s in sources],
        "proposed_changes": proposed_changes(verdict),
        "approval_needed": verdict.get("approval_needed"),
        "reason": verdict.get("reason"),
        "latency_ms": old.get("latency_ms", brain_ms),
        "path": path,
        "check": check,
        "timing": {"trigger_ms": t_trigger, "lookup_ms": t_lookup},
    }
    session.suggestions[sid] = card
    await session.emit(card)

    first_token_ms = None
    async for partial in respond(text, item, verdict, sources, deal):
        if first_token_ms is None:
            first_token_ms = _ms(t0)
        card.update(partial)
        await session.emit(card)
    card["status"] = "done"
    card["timing"]["first_token_ms"] = first_token_ms
    card["timing"]["total_ms"] = _ms(t0)
    await session.emit(card)
    return card

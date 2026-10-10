"""Post-call summary (Granola-style): deterministic facts + optional LLM recap."""
import json

import config
import contract
from agent.llm import client
from session import Session

LABELS = {"tier": "Tier", "discount_pct": "Discount %", "term_years": "Term (years)", "payment_terms": "Payment terms",
          "liability_cap_multiple": "Liability cap (x fees)", "hipaa_baa": "HIPAA BAA", "eu_residency": "EU residency",
          "arr": "ARR"}


async def build(session: Session) -> dict:
    start, end = session.initial_deal, session.deal
    agreed = [f"{s['request']}: {s['counteroffer'] or s['answer']}" for sid in session.accepted
              if (s := session.suggestions.get(sid))]
    handled = set(session.accepted) | set(session.escalated)
    open_items = [f"{s['request']}: escalated to {s.get('approval_needed') or 'manager'} ({s.get('reason')})"
                  for sid in session.escalated if (s := session.suggestions.get(sid))]
    open_items += [f"{s['request']}: not yet resolved" for sid, s in session.suggestions.items() if sid not in handled]
    crm_changes = [{"field": LABELS[k], "old": start[k], "new": end[k]} for k in LABELS if start[k] != end[k]]
    out = {
        "type": "summary",
        "customer": end["customer"],
        "agreed": agreed,
        "open_items": open_items,
        "crm_changes": crm_changes,
        "contract_diff": contract.diff(start, end),
        "next_steps": [],
        "recap": "",
    }
    if client is not None and session.transcript:
        out.update(await _llm_recap(session, out))
    else:
        out["next_steps"] = (["Send updated order form for signature"] if agreed else []) + \
                            [f"Follow up on: {o.split(':')[0]}" for o in open_items]
    return out


async def _llm_recap(session: Session, facts: dict) -> dict:
    convo = "\n".join(f"{t['speaker'].upper()}: {t['text']}" for t in session.transcript[-40:])
    try:
        resp = await client.chat.completions.create(
            model=config.SMART_MODEL,
            messages=[{"role": "system", "content":
                       "Summarise this sales call for the rep's manager. Return JSON: "
                       '{"recap": "2-3 sentences", "next_steps": ["..."]}. Use only the facts given.'},
                      {"role": "user", "content": f"Transcript:\n{convo}\n\nFacts:\n{json.dumps(facts, default=str)}"}],
            temperature=0.2, max_tokens=300, response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return {"recap": data.get("recap", ""), "next_steps": data.get("next_steps", [])}
    except Exception as e:
        print(f"[summary] LLM recap failed: {e}")
        return {}

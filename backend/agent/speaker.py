"""Who said this turn? Instant guess from how the sentence is phrased + conversation signals.

Used when audio comes from a single mic (or typed text), where the source can't tell us.
The LLM (trigger.llm_extract) confirms or overrides low-confidence guesses.
"""
import re
import time

from agent.trigger import heuristic

# (pattern, weight). Positive = customer, negative = rep.
CUES = [
    # customer: asks for concessions, states requirements, objects
    (r"\b(can|could|would|will) you (do|give|offer|match|go|raise|lower|sign|include|throw in|waive|extend|support|provide)\b", 2.0),
    (r"\bdo you (sign|support|offer|have|provide|integrate|allow|do)\b", 1.5),
    (r"\bis (it|there) (possible|any way)\b", 1.5),
    (r"\bwe (need|require|want|expect|'d like|would like|'d need|would need|can't|cannot)\b", 1.5),
    (r"\bwe're (in|a|an|looking|evaluating|comparing|considering|currently)\b", 1.0),
    (r"\bour (legal|security|procurement|cfo|budget|team|board|compliance|it)\b", 1.0),
    (r"\bwhat about\b|\bhow much\b|\bhow about\b", 1.0),
    (r"\b(competitor|other vendor|your competitor|cheaper elsewhere)\b", 1.0),
    (r"\b(send (over|us)|works for us|that works|sounds good to us)\b", 1.0),
    (r"\bwe've (reviewed|looked|read|discussed|talked|been)\b", 1.5),
    # rep: offers, explains policy, discovery questions, reads the card
    (r"\b(we|i) can (offer|do|give|provide|include|sign|extend|move|go|raise|agree)\b", -2.0),
    (r"\b(let me|i'll|i will) (check|get|loop|confirm|send|follow|run|consult|see|take)\b", -2.0),
    (r"\b(our|the) (policy|standard|enterprise (plan|tier)|pricing|template|baa|sla)\b", -1.0),
    (r"\bpre-?approved\b|\bapproved today\b|\bwith approval\b", -1.5),
    (r"\bthat's (included|available|covered)\b|\b(it's|is|are) (included|available|covered)\b", -1.5),
    (r"\byour (enterprise|current|growth|starter) (plan|tier|agreement|contract)\b", -1.0),
    (r"\b(thanks for (joining|making time|your time)|how's your|great question|i understand)\b", -1.0),
    (r"\b(how many|what's your|what is your|are you currently|tell me (about|more)|what (tools|timeline))\b", -1.5),
    (r"\bnimbus\b", -0.5),
]
_CUES = [(re.compile(p), w) for p, w in CUES]
_WORD = re.compile(r"[a-z0-9%]+")
CONFIDENT = 1.5
CONTINUATION_S = 3.0


def _words(t: str) -> set[str]:
    return set(_WORD.findall(t.lower()))


def echo_of_card(text: str, cards: list[dict]) -> float:
    """How much this turn overlaps a suggestion the rep was just shown (reps read cards aloud)."""
    w = _words(text)
    if len(w) < 4:
        return 0.0
    best = 0.0
    for c in cards:
        say = _words(c.get("counteroffer") or "")
        if say:
            best = max(best, len(w & say) / len(w | say))
    return best


def guess(text: str, history: list[dict], cards: list[dict]) -> tuple[str, float]:
    """Returns (speaker, confidence score). |score| >= CONFIDENT means trust it without the LLM."""
    t = text.lower()
    score = sum(w for rx, w in _CUES if rx.search(t))
    if echo_of_card(text, cards[-4:]) >= 0.35:
        score -= 3.0
    if heuristic(text)["items"] and "?" in text:
        score += 0.5  # a question about pricing/terms/compliance is usually the buyer's
    last = next((h for h in reversed(history) if h["speaker"] in ("customer", "rep")), None)
    if last:
        sign = 1 if last["speaker"] == "customer" else -1
        if time.time() - last.get("ts", 0) < CONTINUATION_S:
            score += sign * 1.0   # STT split one person's sentence: same speaker continues
        else:
            score -= sign * 0.5   # otherwise people take turns
    return ("customer" if score > 0 else "rep"), abs(score)

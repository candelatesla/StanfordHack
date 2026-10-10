"""Decides, locally and for free, whether a customer turn is worth any API call.

Sales calls are mostly greetings, rapport and acknowledgements ("how was Lisbon?", "sounds good").
Only turns that ask for something, raise a concern, or touch a topic the Company Brain covers go on to
the LLM / embeddings. Signals:
  - the instant extractor recognised an ask (discount, net 60, HIPAA, pilot, competitor, ...)
  - business relevance: IDF-weighted overlap with the Company Brain's own vocabulary
  - intent: a question, a request, a requirement or a concern
  - small talk: greetings, thanks, weather, travel, sports, sign-offs
"""
import re

from agent.trigger import heuristic, words_to_digits
from brain.ingest import INDEX, tokens

SMALL_TALK = re.compile(r"\b(" + "|".join([
    r"hi", r"hey", r"hello", r"good (morning|afternoon|evening)", r"how are you", r"how's (it going|your|the)",
    r"how was (your|the)", r"nice to (meet|see)", r"good to (see|hear)", r"thanks?( you| so much)?", r"cheers",
    r"can you hear me", r"on mute", r"camera", r"weather", r"snow", r"rain", r"weekend", r"vacation", r"trip",
    r"holiday", r"kids?", r"son", r"daughter", r"family", r"the game", r"coffee", r"lunch", r"traffic",
    r"sounds good", r"perfect", r"great", r"awesome", r"cool", r"no worries", r"talk (soon|next week)",
    r"bye", r"have a (good|great)", r"take care", r"sorry,? i'?m late", r"back-to-back", r"haha", r"lol",
]) + r")\b")
QUESTION = re.compile(r"\?|^(what|how|why|when|where|which|who|can|could|would|will|do|does|did|is|are|should)\b")
REQUEST = re.compile(r"\b(we (need|require|want|expect|'d (need|want|like))|we can't|we cannot|could you|can you|"
                     r"would you|is it possible|any way|must|have to|looking for|"
                     r"(will|'ll|would|'d|going to) need|needs? (a|an|the|your|to)|"
                     r"asking (how|what|whether|if|about|for)|wondering|curious|want(s|ed)? to know|tell me (how|what|about))\b")
CONCERN = re.compile(r"\b(worried|concern|risk|problem|issue|expensive|(no|tight|limited|over|out of) budget|"
                     r"budget (is|freeze)|competitor|cheaper|not sure|"
                     r"hesitant|sign off|sign-off|deal ?breaker|blocker|requirement|compliance)\b")
# Words that appear in the docs but also in everyday chat; they shouldn't make a turn "business"
CHATTER = set("""team teams week weeks time day days year years today good great new people work working call
calls month months next last first also make get one two three four five day morning really much
thing things lot right way want need know think going see look help like just well back""".split())

RELEVANCE = 4.0        # idf-weighted business vocabulary needed alongside a question/request/concern
RELEVANCE_ALONE = 25.0  # ...or this much on its own (background statements rarely need an answer)


def relevance(text: str) -> tuple[float, list[str]]:
    terms = [w for w in dict.fromkeys(tokens(words_to_digits(text.lower()))) if w in INDEX.idf and w not in CHATTER]
    return sum(INDEX.idf[w] for w in terms), terms


def assess(text: str) -> dict:
    """{"candidate": bool, "why": str, "relevance": float, "items": [...fast-path asks...]}"""
    t = text.lower().strip().replace("’", "'").replace("‘", "'")
    items = heuristic(text)["items"]
    rel, terms = relevance(text)
    out = {"candidate": False, "why": "", "relevance": round(rel, 1), "terms": terms, "items": items}
    intent = bool(QUESTION.search(t) or REQUEST.search(t) or CONCERN.search(t))
    asks = [i for i in items if not i.get("topic") or intent or i["category"] == "objection"]
    if asks:  # concrete asks always count; a topic mention ("lots of talk about AI") needs intent
        return out | {"candidate": True, "items": asks,
                      "why": f"recognised ask: {', '.join(i['category'] for i in asks)}"}
    words = re.findall(r"[a-z']+", t)
    if len(words) <= 3:
        return out | {"why": "too short"}
    if rel >= RELEVANCE_ALONE or (intent and rel >= RELEVANCE):
        return out | {"candidate": True, "why": f"{'question/request' if intent else 'statement'} on {', '.join(terms[:4])}"}
    if SMALL_TALK.search(t):
        return out | {"why": "small talk"}
    return out | {"why": "no business topic" if intent else "no question or business topic"}

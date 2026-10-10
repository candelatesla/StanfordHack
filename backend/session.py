"""Single live-call session + event bus (one call at a time is plenty for the demo)."""
import asyncio
import itertools
import uuid
import time

from fastapi import WebSocket

import crm
from voice import VoiceRoster


class Session:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.reset()

    def reset(self):
        crm.init(reset=True)
        self.deal_id = crm.DEMO_DEAL["id"]
        self.initial_deal = crm.get_deal(self.deal_id)
        self.transcript: list[dict] = []
        self.suggestions: dict[str, dict] = {}
        self.accepted: list[str] = []
        self.escalated: list[str] = []
        self.last_customer_turn: dict | None = None
        self.roster = VoiceRoster()     # voices heard on this call (single-mic mode)
        self.calibrate_until = 0.0      # next voiced turn before this time is the rep's calibration
        self.stats = {"turns": 0, "skipped": 0, "llm_calls": 0, "embed_calls": 0, "echoes": 0}
        self.customer_partial = ("", 0.0)  # latest in-progress customer text (tab audio) + when it arrived
        self._ids = itertools.count(1)
        self._turn_ids = itertools.count(1)
        self.call_id = uuid.uuid4().hex  # late background work from a previous call is dropped
        self.started = time.time()

    def next_id(self) -> str:
        return f"s{next(self._ids)}"

    @property
    def deal(self) -> dict:
        return crm.get_deal(self.deal_id)

    async def emit(self, event: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def add_turn(self, speaker: str, text: str, auto: bool = False, confident: bool = True) -> dict:
        """auto: speaker was inferred (single mic / typed), not known from the audio source."""
        turn = {"type": "transcript", "id": f"t{next(self._turn_ids)}", "call": self.call_id, "speaker": speaker, "text": text,
                "is_final": True, "auto": auto, "confident": confident, "ts": time.time()}
        self.transcript.append(turn)
        return turn

    def is_current(self, turn: dict) -> bool:
        return turn.get("call") == self.call_id

    def turn(self, turn_id: str) -> dict | None:
        return next((t for t in self.transcript if t["id"] == turn_id), None)

    async def set_speaker(self, turn: dict, speaker: str, manual: bool = False):
        changed = turn["speaker"] != speaker
        turn["speaker"] = speaker
        if manual:
            turn["auto"], turn["confident"] = False, True
        if changed or manual:
            await self.emit({"type": "transcript_update", "id": turn["id"], "speaker": speaker, "via": turn.get("via"),
                             "auto": turn["auto"]})

    async def emit_stats(self):
        await self.emit({"type": "stats", **self.stats, "voices": self.roster.summary()})

    async def remove_card(self, sid: str):
        if self.suggestions.pop(sid, None) is not None:
            await self.emit({"type": "suggestion_removed", "id": sid})

    def is_echo(self, text: str, window_s: float = 6.0) -> bool:
        """Rep mic picking up the customer's audio from the speakers (tab + mic mode)."""
        words = _words(text)
        if not words:
            return False
        now = time.time()
        heard = [t["text"] for t in self.transcript
                 if t["speaker"] == "customer" and now - t["ts"] <= window_s]
        partial, ts = self.customer_partial
        if partial and now - ts <= window_s:  # the tab's own transcript may not be final yet
            heard.append(partial)
        # The two streams split sentences differently (one mic turn can span two tab turns), so ask "is most
        # of the mic text in what the customer just said, taken together?" Short fragments must be fully
        # contained. Dropping a rep's "sounds good" costs nothing (rep turns make no cards); a customer
        # fragment labelled rep can lose a card.
        other = set().union(*map(_words, heard))
        return len(words & other) / len(words) >= (0.7 if len(words) >= 3 else 1.0)


def _words(text: str) -> set[str]:
    return {w.strip(".,!?;:'\"").lower() for w in text.split()} - {""}


SESSION = Session()
LOCK = asyncio.Lock()

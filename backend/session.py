"""Single live-call session + event bus (one call at a time is plenty for the demo)."""
import asyncio
import itertools
import time

from fastapi import WebSocket

import crm


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
        self._ids = itertools.count(1)
        self._turn_ids = itertools.count(1)
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
        turn = {"type": "transcript", "id": f"t{next(self._turn_ids)}", "speaker": speaker, "text": text,
                "is_final": True, "auto": auto, "confident": confident, "ts": time.time()}
        self.transcript.append(turn)
        return turn

    def turn(self, turn_id: str) -> dict | None:
        return next((t for t in self.transcript if t["id"] == turn_id), None)

    async def set_speaker(self, turn: dict, speaker: str, manual: bool = False):
        changed = turn["speaker"] != speaker
        turn["speaker"] = speaker
        if manual:
            turn["auto"], turn["confident"] = False, True
        if changed or manual:
            await self.emit({"type": "transcript_update", "id": turn["id"], "speaker": speaker,
                             "auto": turn["auto"]})

    async def remove_card(self, sid: str):
        if self.suggestions.pop(sid, None) is not None:
            await self.emit({"type": "suggestion_removed", "id": sid})

    def is_echo(self, text: str, window_s: float = 6.0) -> bool:
        """Rep mic picking up the customer's audio from the speakers (tab + mic mode)."""
        words = set(text.lower().split())
        now = time.time()
        for t in reversed(self.transcript):
            if now - t["ts"] > window_s:
                break
            if t["speaker"] == "customer":
                other = set(t["text"].lower().split())
                if words and len(words & other) / len(words | other) >= 0.6:
                    return True
        return False


SESSION = Session()
LOCK = asyncio.Lock()

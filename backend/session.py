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

    def add_turn(self, speaker: str, text: str) -> dict:
        turn = {"type": "transcript", "speaker": speaker, "text": text, "is_final": True, "ts": time.time()}
        self.transcript.append(turn)
        return turn


SESSION = Session()
LOCK = asyncio.Lock()

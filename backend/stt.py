"""ElevenLabs speech-to-text. One stream per speaker (tab audio = customer, mic = rep).

realtime: Scribe realtime WebSocket (partials + VAD-committed finals).
batch:    local energy VAD cuts utterances, each sent to the batch STT endpoint. Used automatically
          if the realtime socket can't connect.
Input audio is always 16kHz mono PCM16.
"""
import asyncio
import base64
import io
import json
import time
import wave
from urllib.parse import urlencode
from typing import Awaitable, Callable

import httpx
import numpy as np
import websockets

import config

OnText = Callable[[str, bool], Awaitable[None]]  # (text, is_final)
SAMPLE_RATE = 16000


class RealtimeSTT:
    URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"

    def __init__(self, on_text: OnText):
        self.on_text = on_text
        self.ws = None
        self.reader: asyncio.Task | None = None
        self.dead = False
        self.speech_since_msg = 0.0
        self.unanswered_since = 0.0

    # While someone is talking the server streams partial transcripts within ~0.5s, so speech that has
    # gone unanswered this long means the socket is silently dead. (Silence alone proves nothing.)
    STALL_MIN_SPEECH_S = 1.0
    STALL_WAIT_S = 2.5

    @property
    def stalled(self) -> bool:
        return (self.speech_since_msg >= self.STALL_MIN_SPEECH_S
                and time.monotonic() - self.unanswered_since > self.STALL_WAIT_S)

    async def start(self):
        params = [("model_id", config.STT_REALTIME_MODEL), ("audio_format", "pcm_16000"),
                  ("commit_strategy", "vad"), ("vad_silence_threshold_secs", "0.6"), ("language_code", "en")]
        params += [("keyterms", k) for k in config.STT_KEYTERMS]
        self.ws = await websockets.connect(f"{self.URL}?{urlencode(params)}",
                                           additional_headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                                           open_timeout=5, ping_interval=5, ping_timeout=5)
        self.speech_since_msg = 0.0
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                self.speech_since_msg = 0.0
                msg = json.loads(raw)
                kind = msg.get("message_type") or msg.get("type", "")
                text = (msg.get("text") or "").strip()
                if kind == "partial_transcript" and text:
                    await self.on_text(text, False)
                elif kind == "committed_transcript" and text:
                    await self.on_text(text, True)
                elif "error" in msg:
                    print(f"[stt] realtime {kind}: {msg['error']}")
        except websockets.ConnectionClosed as e:
            print(f"[stt] realtime connection closed: {e}")
        finally:
            self.dead = True

    async def feed(self, pcm: bytes):
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if len(x) and float(np.sqrt(np.mean(x ** 2))) > 500:
            if self.speech_since_msg == 0:
                self.unanswered_since = time.monotonic()
            self.speech_since_msg += len(x) / SAMPLE_RATE
        await self.ws.send(json.dumps({"message_type": "input_audio_chunk",
                                       "audio_base_64": base64.b64encode(pcm).decode(),
                                       "commit": False, "sample_rate": SAMPLE_RATE}))

    async def close(self):
        self.dead = True
        if self.reader:
            self.reader.cancel()
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass


class BatchSTT:
    URL = "https://api.elevenlabs.io/v1/speech-to-text"
    FRAME = SAMPLE_RATE // 10  # 100ms
    SPEECH_RMS = 500
    END_SILENCE_FRAMES = 7     # 700ms of silence ends an utterance
    MIN_SPEECH_FRAMES = 3
    MAX_FRAMES = 150           # 15s hard cut

    dead = False  # batch never drops a connection; failures are per-utterance
    stalled = False

    def __init__(self, on_text: OnText):
        self.on_text = on_text
        self.pending = b""
        self.utterance: list[bytes] = []
        self.speech_frames = 0
        self.silence = 0
        self.http = httpx.AsyncClient(timeout=20)
        self.tasks: set[asyncio.Task] = set()

    async def start(self):
        pass

    async def feed(self, pcm: bytes):
        self.pending += pcm
        step = self.FRAME * 2
        while len(self.pending) >= step:
            frame, self.pending = self.pending[:step], self.pending[step:]
            rms = float(np.sqrt(np.mean(np.frombuffer(frame, dtype=np.int16).astype(np.float32) ** 2)))
            speaking = rms > self.SPEECH_RMS
            if speaking or self.utterance:
                self.utterance.append(frame)
            if speaking:
                self.speech_frames += 1
                self.silence = 0
                if self.speech_frames == self.MIN_SPEECH_FRAMES:
                    await self.on_text("…", False)
            elif self.utterance:
                self.silence += 1
            if self.utterance and (self.silence >= self.END_SILENCE_FRAMES or len(self.utterance) >= self.MAX_FRAMES):
                self._flush()

    def _flush(self):
        audio, enough = b"".join(self.utterance), self.speech_frames >= self.MIN_SPEECH_FRAMES
        self.utterance, self.speech_frames, self.silence = [], 0, 0
        if enough:
            t = asyncio.create_task(self._transcribe(audio))
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)

    async def _transcribe(self, pcm: bytes):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm)
        try:
            r = await self.http.post(self.URL, headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                                     data={"model_id": config.STT_BATCH_MODEL},
                                     files={"file": ("turn.wav", buf.getvalue(), "audio/wav")})
            r.raise_for_status()
            text = (r.json().get("text") or "").strip()
            if text:
                await self.on_text(text, True)
        except Exception as e:
            print(f"[stt] batch transcription failed: {e}")

    async def close(self):
        self._flush()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.http.aclose()


async def _open(on_text: OnText):
    if config.STT_MODE == "realtime":
        stream = RealtimeSTT(on_text)
        try:
            await stream.start()
            print("[stt] realtime stream connected")
            return stream
        except Exception as e:
            print(f"[stt] realtime connect failed ({e!r}); falling back to batch")
    stream = BatchSTT(on_text)
    await stream.start()
    return stream


class ResilientSTT:
    """Keeps transcription alive for the whole call. If the upstream STT socket dies or silently stalls,
    reopen it (realtime first, then batch) and replay the audio since the last finished sentence,
    so nothing the customer said in the meantime is lost."""

    REPLAY_CHUNKS = 200  # up to ~20s of 100ms chunks

    def __init__(self, on_text: OnText):
        self.reconnects = 0
        self.since_commit: list[bytes] = []
        self._user_on_text = on_text
        self.inner = None

    async def on_text(self, text: str, is_final: bool):
        if is_final:
            self.since_commit.clear()
        await self._user_on_text(text, is_final)

    async def start(self):
        self.inner = await _open(self.on_text)

    async def feed(self, pcm: bytes):
        if self.inner.dead or self.inner.stalled:
            self.reconnects += 1
            why = "stalled" if not self.inner.dead else "lost"
            print(f"[stt] upstream {why}; reconnect #{self.reconnects}, replaying {len(self.since_commit) / 10:.1f}s")
            await self.inner.close()
            self.inner = await _open(self.on_text)
            for chunk in list(self.since_commit):
                await self._send(chunk)
        self.since_commit.append(pcm)
        del self.since_commit[:-self.REPLAY_CHUNKS]
        await self._send(pcm)

    async def _send(self, pcm: bytes):
        try:
            await self.inner.feed(pcm)
        except Exception as e:
            print(f"[stt] feed failed ({e!r}); will reconnect")
            self.inner.dead = True

    async def close(self):
        if self.inner:
            await self.inner.close()


async def open_stream(on_text: OnText):
    stream = ResilientSTT(on_text)
    await stream.start()
    return stream

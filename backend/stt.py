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
import wave
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

    async def start(self):
        params = f"model_id={config.STT_REALTIME_MODEL}&audio_format=pcm_16000&commit_strategy=vad"
        self.ws = await websockets.connect(f"{self.URL}?{params}",
                                           additional_headers={"xi-api-key": config.ELEVENLABS_API_KEY},
                                           open_timeout=5)
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                kind = msg.get("message_type") or msg.get("type", "")
                text = (msg.get("text") or "").strip()
                if kind == "partial_transcript" and text:
                    await self.on_text(text, False)
                elif kind.startswith("committed_transcript") and text:
                    await self.on_text(text, True)
                elif "error" in kind:
                    print(f"[stt] realtime error: {msg}")
        except websockets.ConnectionClosed:
            pass

    async def feed(self, pcm: bytes):
        await self.ws.send(json.dumps({"message_type": "input_audio_chunk",
                                       "audio_base_64": base64.b64encode(pcm).decode(),
                                       "commit": False, "sample_rate": SAMPLE_RATE}))

    async def close(self):
        if self.ws:
            await self.ws.close()
        if self.reader:
            self.reader.cancel()


class BatchSTT:
    URL = "https://api.elevenlabs.io/v1/speech-to-text"
    FRAME = SAMPLE_RATE // 10  # 100ms
    SPEECH_RMS = 500
    END_SILENCE_FRAMES = 7     # 700ms of silence ends an utterance
    MIN_SPEECH_FRAMES = 3
    MAX_FRAMES = 150           # 15s hard cut

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


async def open_stream(on_text: OnText):
    if config.STT_MODE == "realtime":
        stream = RealtimeSTT(on_text)
        try:
            await stream.start()
            print("[stt] realtime stream connected")
            return stream
        except Exception as e:
            print(f"[stt] realtime connect failed ({e}); falling back to batch")
    stream = BatchSTT(on_text)
    await stream.start()
    return stream

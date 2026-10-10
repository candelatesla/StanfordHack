"""Who is speaking, from the voice itself (for single-mic calls).

Each utterance's audio -> 256-d voiceprint (WeSpeaker ResNet34 via sherpa-onnx, local CPU, no API call).
Voiceprints are clustered online into speakers. Each speaker then needs a ROLE (rep/customer), learned from:
  - calibration: the rep says one sentence after clicking "Calibrate my voice" (strongest)
  - manual corrections in the UI (click a speaker label)
  - the LLM's speaker call on turns that needed it anyway, and confident text cues
  - exclusivity: once one voice is the rep, other voices are customers
After a voice's role is established, its turns are labelled by voice alone, so small talk costs nothing.

Measured on ElevenLabs voices: same speaker 0.82-0.88 cosine, different speakers 0.53-0.74.
"""
import threading

import numpy as np

import config

try:
    import sherpa_onnx
except ImportError:  # optional dependency; text cues + LLM still work without it
    sherpa_onnx = None

MODEL_PATH = config.ROOT / "models" / "wespeaker_en_voxceleb_resnet34.onnx"
MODEL_URL = "https://huggingface.co/csukuangfj/speaker-embedding-models/resolve/main/wespeaker_en_voxceleb_resnet34.onnx"
SAMPLE_RATE = 16000
MIN_VOICED_S = 0.8      # shorter clips give unreliable voiceprints
SAME_SPEAKER = 0.72     # below this to every known voice -> a new speaker
MERGE_SPEAKERS = 0.78   # two voice clusters whose averages converge this close are one person
MIN_NEW_SPEAKER_S = 1.5  # only a clip at least this long may register a new speaker
RELIABLE_S = 1.3         # shorter clips (STT fragments like "can you fill that out?") give shaky voiceprints
CONTINUATION_S = 3.0     # a short fragment this soon after a turn is usually the same person continuing
ESTABLISHED = 3.0       # how far the rep's voice must lead the others in evidence before voices decide
MAX_SPEAKERS = 4


class VoiceModel:
    def __init__(self):
        self._extractor = None
        self._lock = threading.Lock()
        self.error = ""

    @property
    def available(self) -> bool:
        return self._load() is not None

    def _load(self):
        if self._extractor is None and not self.error:
            if sherpa_onnx is None:
                self.error = "sherpa-onnx not installed"
            elif not MODEL_PATH.exists():
                self.error = f"voice model missing; run: python tools/get_voice_model.py"
            else:
                cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL_PATH), num_threads=2)
                self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
            if self.error:
                print(f"[voice] disabled: {self.error}")
        return self._extractor

    def embed(self, pcm16: bytes) -> np.ndarray | None:
        """L2-normalised voiceprint, or None if the clip is too short or the model is unavailable."""
        ex = self._load()
        if ex is None or len(pcm16) < MIN_VOICED_S * SAMPLE_RATE * 2:
            return None
        audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            stream = ex.create_stream()
            stream.accept_waveform(SAMPLE_RATE, audio)
            stream.input_finished()
            v = np.array(ex.compute(stream), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)


MODEL = VoiceModel()


class VoicedBuffer:
    """Collects only the voiced 100ms frames of the current utterance (adaptive noise floor)."""

    def __init__(self):
        self.frames: list[bytes] = []
        self.floor = 300.0

    def add(self, pcm: bytes):
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
        if not len(x):
            return
        rms = float(np.sqrt(np.mean(x ** 2)))
        if rms < 1.5 * self.floor:
            self.floor = 0.95 * self.floor + 0.05 * max(rms, 50.0)
        if rms > max(250.0, 2.5 * self.floor) and len(self.frames) < 150:  # cap at 15s
            self.frames.append(pcm)

    def take(self) -> bytes:
        audio, self.frames = b"".join(self.frames), []
        return audio


class VoiceRoster:
    """The voices heard on this call and what role each one has. Speaker ids are stable; a merged
    speaker's old id keeps resolving to the speaker it was folded into."""

    def __init__(self):
        self.speakers: dict[int, dict] = {}
        self.alias: dict[int, int] = {}
        self._next = 0

    def _sid(self, sid: int | None) -> int | None:
        while sid in self.alias:
            sid = self.alias[sid]
        return sid if sid in self.speakers else None

    def identify(self, v: np.ndarray, seconds: float = 99.0) -> tuple[int, float]:
        """Assign a voiceprint to a known speaker (updating it) or register a new one."""
        sims = {sid: float(sp["centroid"] @ v) for sid, sp in self.speakers.items()}
        best = max(sims, key=sims.get) if sims else None
        can_be_new = seconds >= MIN_NEW_SPEAKER_S and len(self.speakers) < MAX_SPEAKERS
        if best is not None and (sims[best] >= SAME_SPEAKER or not can_be_new):
            if seconds < RELIABLE_S:
                return best, sims[best]  # too short to trust: match, but don't let it drift the profile
            sp = self.speakers[best]
            c = sp["centroid"] * sp["n"] + v
            sp["centroid"], sp["n"] = c / np.linalg.norm(c), sp["n"] + 1
            return self._merge(best), sims[best]
        sid, self._next = self._next, self._next + 1
        self.speakers[sid] = {"centroid": v, "n": 1, "votes": {"rep": 0.0, "customer": 0.0}, "pinned": None}
        return sid, 1.0

    def _merge(self, sid: int) -> int:
        """One person's first utterances can land in two clusters; fold them together once they converge."""
        sp = self.speakers[sid]
        for oid, other in list(self.speakers.items()):
            if oid == sid or float(sp["centroid"] @ other["centroid"]) < MERGE_SPEAKERS:
                continue
            if sp["pinned"] and other["pinned"] and sp["pinned"] != other["pinned"]:
                continue  # the user said these are different people
            keep, drop = (sid, oid) if sp["n"] >= other["n"] else (oid, sid)
            a, b = self.speakers[keep], self.speakers.pop(drop)
            c = a["centroid"] * a["n"] + b["centroid"] * b["n"]
            a["centroid"], a["n"] = c / np.linalg.norm(c), a["n"] + b["n"]
            for r in a["votes"]:
                a["votes"][r] += b["votes"][r]
            a["pinned"] = a["pinned"] or b["pinned"]
            self.alias[drop] = keep
            print(f"[voice] merged voice {drop} into {keep}: same person")
            return keep
        return sid

    def vote(self, sid: int | None, role: str, weight: float):
        sid = self._sid(sid)
        if sid is not None and role in ("rep", "customer"):
            self.speakers[sid]["votes"][role] += weight

    def pin(self, sid: int | None, role: str):
        sid = self._sid(sid)
        if sid is None:
            return
        self.speakers[sid]["pinned"] = role
        if role == "rep":  # one rep per call: any other voice pinned as rep was a mis-split of this one
            for oid, sp in self.speakers.items():
                if oid != sid and sp["pinned"] == "rep":
                    sp["pinned"] = None

    @staticmethod
    def _evidence(sp: dict) -> float:
        return sp["votes"]["rep"] - sp["votes"]["customer"]

    def role(self, sid: int | None) -> tuple[str | None, float]:
        """(role, strength). strength >= ESTABLISHED means trust the voice without text cues or the LLM.

        Roles are decided jointly: the rep is the voice whose rep-evidence clearly beats every other voice's,
        so one wrong early vote can't flip a voice the rest of the conversation contradicts."""
        sid = self._sid(sid)
        if sid is None:
            return None, 0.0
        me = self.speakers[sid]
        if me["pinned"]:
            return me["pinned"], 9.0
        pinned_rep = [o for o, sp in self.speakers.items() if sp["pinned"] == "rep"]
        if pinned_rep:
            return "customer", 9.0  # the rep's voice was calibrated/corrected and this isn't it
        if len(self.speakers) == 1:
            e = self._evidence(me)
            return (("rep" if e > 0 else "customer") if abs(e) >= ESTABLISHED else None), abs(e)
        ranked = sorted(self.speakers, key=lambda o: self._evidence(self.speakers[o]), reverse=True)
        lead = self._evidence(self.speakers[ranked[0]]) - self._evidence(self.speakers[ranked[1]])
        if lead < ESTABLISHED:
            return None, lead
        return ("rep" if sid == ranked[0] else "customer"), lead

    def summary(self) -> list[dict]:
        return [{"id": sid, "role": self.role(sid)[0], "turns": sp["n"], "pinned": sp["pinned"]}
                for sid, sp in self.speakers.items()]

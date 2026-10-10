"""Download the speaker-recognition model used for single-mic voice identification (26.5 MB, one time).
    backend/.venv/Scripts/python tools/get_voice_model.py"""
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import voice  # noqa: E402

if voice.MODEL_PATH.exists():
    print(f"already present: {voice.MODEL_PATH}")
else:
    voice.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = voice.MODEL_PATH.with_suffix(".part")
    print(f"downloading {voice.MODEL_URL}")
    urllib.request.urlretrieve(voice.MODEL_URL, tmp)
    tmp.rename(voice.MODEL_PATH)
    print(f"saved {voice.MODEL_PATH} ({voice.MODEL_PATH.stat().st_size / 1e6:.1f} MB)")

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")
load_dotenv(ROOT / ".env")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
NEBIUS_API_KEY = os.getenv("NEBIUS_API_KEY", "")
NEBIUS_BASE_URL = os.getenv("NEBIUS_BASE_URL") or "https://api.tokenfactory.nebius.com/v1/"
FAST_MODEL = os.getenv("FAST_MODEL") or "meta-llama/Meta-Llama-3.1-8B-Instruct-fast"
SMART_MODEL = os.getenv("SMART_MODEL") or "meta-llama/Llama-3.3-70B-Instruct-fast"
EMBED_MODEL = os.getenv("EMBED_MODEL", "")  # empty = auto-pick from /v1/models at startup

# realtime = ElevenLabs streaming STT (auto-falls back to batch on connect failure)
# batch    = energy-VAD segments sent to the ElevenLabs batch STT endpoint
STT_MODE = os.getenv("STT_MODE", "realtime")
STT_REALTIME_MODEL = os.getenv("STT_REALTIME_MODEL", "scribe_v2_realtime")
STT_BATCH_MODEL = os.getenv("STT_BATCH_MODEL", "scribe_v1")
STT_KEYTERMS = ["Nimbus", "HIPAA", "BAA", "net 60", "net 30", "SOC 2", "GDPR", "liability cap", "Enterprise", "ARR"]
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# If the trigger model hasn't answered within this budget, use the regex extractor instead.
TRIGGER_TIMEOUT_S = float(os.getenv("TRIGGER_TIMEOUT_S", "0.35"))

BRAIN_DIR = ROOT / "brain"
DOCS_DIR = BRAIN_DIR / "docs"
RULES_PATH = BRAIN_DIR / "rules.json"
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "crm.sqlite3"))
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"

LLM_ENABLED = bool(NEBIUS_API_KEY)

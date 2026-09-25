"""Configurazione centralizzata da .env + percorsi canonici."""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path("/opt/reel-agent")
load_dotenv(BASE_DIR / ".env")

CFG = {
    "APP_VERSION": "0.1.0",
    "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),
    "CLAUDE_MODEL": os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"),  # legacy
    "CLAUDE_MODEL_FAST": os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5-20251001"),
    "CLAUDE_MODEL_SMART": os.getenv("CLAUDE_MODEL_SMART", "claude-sonnet-5"),
    "CLAUDE_MAX_TOKENS": int(os.getenv("CLAUDE_MAX_TOKENS", "3000")),
    "WHISPER_MODEL": os.getenv("WHISPER_MODEL", "small"),
    "WHISPER_COMPUTE": os.getenv("WHISPER_COMPUTE", "int8"),
    "HOST": os.getenv("HOST", "0.0.0.0"),
    "PORT": int(os.getenv("PORT", "5000")),

    "BASE_DIR": str(BASE_DIR),
    "APP_DIR": str(BASE_DIR / "app"),
    "MEDIA_DIR": str(BASE_DIR / "media"),
    "RAW_DIR": str(BASE_DIR / "media" / "raw"),
    "TRIMMED_DIR": str(BASE_DIR / "media" / "trimmed"),
    "FRAMES_DIR": str(BASE_DIR / "media" / "frames"),
    "STYLE_REF_DIR": str(BASE_DIR / "media" / "style_ref"),
    "VOICEOVER_DIR": str(BASE_DIR / "media" / "voiceover"),
    "RENDERS_DIR": str(BASE_DIR / "media" / "renders"),
    "STYLES_DIR": str(BASE_DIR / "styles"),
    "LOGS_DIR": str(BASE_DIR / "logs"),
    "MODELS_DIR": str(BASE_DIR / "models"),
    "DB_PATH": str(BASE_DIR / "jobs" / "reel.db"),
}

def require_api_key() -> str:
    key = CFG["ANTHROPIC_API_KEY"]
    if not key or key.startswith("incolla"):
        raise RuntimeError("ANTHROPIC_API_KEY non impostata in /opt/reel-agent/.env")
    return key

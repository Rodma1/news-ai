from __future__ import annotations

import os
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


class Settings:
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
    INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "change-me-please")

    LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
    LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

    CRAWL_TIMES = [t.strip() for t in os.environ.get("CRAWL_TIMES", "07:00,12:00,20:00").split(",") if t.strip()]
    DIGEST_TIME = os.environ.get("DIGEST_TIME", "21:00")
    MAX_AI_PER_DAY = int(os.environ.get("MAX_AI_PER_DAY", "50"))

    DB_PATH = BACKEND_DIR / "data" / "news.db"
    FRONTEND_DIST = BACKEND_DIR.parent / "frontend" / "dist"
    PORT = int(os.environ.get("PORT", "8001"))


settings = Settings()
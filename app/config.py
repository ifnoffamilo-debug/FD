from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]
    ai_api_key: str | None
    ai_base_url: str
    ai_model: str
    database_path: Path
    timezone: str


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        ids.add(int(item))
    return frozenset(ids)


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задан BOT_TOKEN в файле .env")

    admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))
    if not admin_ids:
        raise RuntimeError("Не задан ADMIN_IDS в файле .env")

    db_value = os.getenv("DATABASE_PATH", "data/finance.db").strip()
    db_path = Path(db_value)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    ai_key = os.getenv("AI_API_KEY", "").strip() or None

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        ai_api_key=ai_key,
        ai_base_url=os.getenv("AI_BASE_URL", "https://api.groq.com/openai/v1").strip().rstrip("/") or "https://api.groq.com/openai/v1",
        ai_model=os.getenv("AI_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile",
        database_path=db_path,
        timezone=os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow",
    )

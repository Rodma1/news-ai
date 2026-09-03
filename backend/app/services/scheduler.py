from __future__ import annotations

import asyncio

from datetime import datetime

from ..config import settings
from ..models import SessionLocal, Setting
from . import ai, crawler


def _flag_get(key: str) -> str:
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        return row.value if row else ""
    finally:
        db.close()


def _flag_set(key: str, value: str) -> None:
    db = SessionLocal()
    try:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
        db.commit()
    finally:
        db.close()


def _crawl_times() -> list[str]:
    raw = ai.get_setting("crawl_times", ",".join(settings.CRAWL_TIMES))
    return [t.strip() for t in raw.split(",") if t.strip()]


def _digest_time() -> str:
    return ai.get_setting("digest_time", settings.DIGEST_TIME)


def run_crawl_and_process() -> dict:
    stats = crawler.crawl_all()
    stats["ai"] = ai.process_pending()
    return stats


async def scheduler_loop() -> None:
    loop = asyncio.get_running_loop()
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            hm = now.strftime("%H:%M")
            for slot in _crawl_times():
                key = f"crawled_{today}_{slot}"
                if hm >= slot and _flag_get(key) != "1":
                    _flag_set(key, "1")
                    await loop.run_in_executor(None, run_crawl_and_process)
            digest_key = f"digest_{today}"
            if hm >= _digest_time() and _flag_get(digest_key) != "1":
                _flag_set(digest_key, "1")
                await loop.run_in_executor(None, ai.generate_digest, None)
        except Exception:
            pass
        await asyncio.sleep(60)
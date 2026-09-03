from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings
from .models import AdminUser, Article, Digest, SessionLocal, Source, Base, engine

DEFAULT_SOURCES = [
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "rss"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "rss"),
    ("机器之心", "https://www.jiqizhixin.com/rss", "rss"),
    ("量子位", "https://www.qbitai.com/feed", "rss"),
    ("Hacker News", "https://hnrss.org/frontpage?points=100", "rss"),
    ("arXiv cs.AI", "https://rss.arxiv.org/rss/cs.AI", "rss"),
    ("Reddit r/MachineLearning", "https://www.reddit.com/r/MachineLearning/.rss", "rss"),
]


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        cols = [row[1] for row in db.execute(text("PRAGMA table_info(articles)"))]
        if "title_zh" not in cols:
            db.execute(text("ALTER TABLE articles ADD COLUMN title_zh VARCHAR(512)"))
        if "content" not in cols:
            db.execute(text("ALTER TABLE articles ADD COLUMN content TEXT"))
        if not db.query(Source).first():
            for name, url, type_ in DEFAULT_SOURCES:
                db.add(Source(name=name, url=url, type=type_))
        if not db.query(AdminUser).first():
            salt = secrets.token_hex(16)
            db.add(AdminUser(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD, salt),
                salt=salt,
            ))
        db.commit()
    finally:
        db.close()
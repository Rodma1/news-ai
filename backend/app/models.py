from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(512), nullable=False)
    title_zh = Column(String(512), nullable=True)
    url = Column(String(1024), nullable=False, unique=True, index=True)
    source = Column(String(128), default="", index=True)
    source_type = Column(String(32), default="push")
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, default=datetime.now, index=True)
    summary_raw = Column(Text, default="")
    content = Column(Text, default="")
    ai_summary = Column(Text, nullable=True)
    category = Column(String(64), nullable=True, index=True)
    tags = Column(String(256), default="")
    score = Column(Integer, nullable=True, index=True)
    entities = Column(String(256), default="")
    ai_status = Column(String(16), default="pending", index=True)
    ai_retries = Column(Integer, default=0)


class Digest(Base):
    __tablename__ = "digests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), unique=True, index=True, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    url = Column(String(1024), nullable=False)
    type = Column(String(16), default="rss")
    enabled = Column(Boolean, default=True)
    last_crawled_at = Column(DateTime, nullable=True)
    last_status = Column(String(16), default="")


class AdminUser(Base):
    __tablename__ = "admin_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    salt = Column(String(64), nullable=False)


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    note = Column(String(128), default="")
    created_at = Column(DateTime, default=datetime.now)


class Setting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, default="")


class Guestbook(Base):
    __tablename__ = "guestbook"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(40), default="匿名")
    contact = Column(String(120), default="")
    content = Column(Text, nullable=False)
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


class PageView(Base):
    __tablename__ = "page_views"

    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String(256), default="", index=True)
    article_id = Column(Integer, nullable=True, index=True)
    ip = Column(String(64), default="")
    created_at = Column(DateTime, default=datetime.now, index=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
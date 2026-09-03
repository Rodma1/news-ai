from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from ..config import settings
from ..models import ApiToken, SessionLocal
from ..services import crawler

router = APIRouter(prefix="/api/v1", tags=["ingest"])

_rate: dict[str, deque] = defaultdict(deque)
RATE_LIMIT = 120
RATE_WINDOW = 60


def _valid_token(token: str) -> bool:
    if not token:
        return False
    db = SessionLocal()
    try:
        if db.query(ApiToken).filter(ApiToken.token == token).first():
            return True
    finally:
        db.close()
    return token == settings.INGEST_TOKEN


def _check_rate(key: str) -> bool:
    now = time.time()
    bucket = _rate[key]
    while bucket and now - bucket[0] > RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    return True


@router.post("/news")
def push_news(
    request: Request,
    payload: Any,
    authorization: str = Header(default=""),
):
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not _valid_token(token) or not _check_rate(token[:8]):
        raise HTTPException(status_code=401, detail="invalid token or rate limited")
    items = payload if isinstance(payload, list) else [payload]
    normalized = []
    for it in items:
        if not isinstance(it, dict):
            continue
        normalized.append({
            "title": it.get("title", ""),
            "url": it.get("url", ""),
            "source": it.get("source", ""),
            "source_type": "push",
            "published": it.get("published_at", "") or it.get("published", ""),
            "summary": it.get("summary", ""),
            "content": it.get("content", ""),
            "tags": it.get("tags") or [],
            "title_zh": it.get("title_zh", ""),
            "ai_summary": it.get("ai_summary", ""),
            "category": it.get("category", ""),
            "score": it.get("score"),
            "entities": it.get("entities") or [],
        })
    if not normalized:
        raise HTTPException(status_code=422, detail="no valid items, title and url are required")
    inserted, duplicated, updated = crawler.insert_articles(normalized, source_type="push")
    return {"inserted": inserted, "duplicates": duplicated, "updated": updated, "received": len(normalized)}


@router.post("/news/update")
def update_news(
    request: Request,
    payload: Any,
    authorization: str = Header(default=""),
):
    from ..models import Article, SessionLocal
    from ..services.ai import CATEGORIES

    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not _valid_token(token):
        raise HTTPException(status_code=401, detail="invalid token")
    items = payload if isinstance(payload, list) else [payload]

    def _apply(it: dict):
        if not isinstance(it, dict):
            return {"ok": False, "error": "item must be an object"}
        raw_url = str(it.get("url") or "").strip()
        if not raw_url.startswith("http"):
            return {"ok": False, "error": "url is required"}
        url = crawler.normalize_url(raw_url)
        fields = {}
        if "title_zh" in it:
            fields["title_zh"] = crawler._strip_html(str(it.get("title_zh") or ""))[:500] or None
        if "ai_summary" in it:
            fields["ai_summary"] = crawler._strip_html(str(it.get("ai_summary") or ""))[:300] or None
        if "category" in it:
            cat = str(it.get("category") or "")
            fields["category"] = cat if cat in CATEGORIES else None
        if "score" in it:
            try:
                fields["score"] = max(1, min(5, int(it.get("score")))) if it.get("score") is not None else None
            except (TypeError, ValueError):
                fields["score"] = None
        if "tags" in it:
            fields["tags"] = ",".join(str(t) for t in (it.get("tags") or [])[:5])[:250]
        if "entities" in it:
            fields["entities"] = ",".join(str(e) for e in (it.get("entities") or [])[:5])[:250]
        if "summary" in it:
            fields["summary_raw"] = crawler._strip_html(str(it.get("summary") or ""))[:2000]
        if "content" in it:
            fields["content"] = crawler._strip_html(str(it.get("content") or ""))[:50000]
        if not fields:
            return {"ok": False, "error": "no updatable fields", "url": url}
        db = SessionLocal()
        try:
            art = db.query(Article).filter(Article.url == url).first()
            if not art:
                return {"ok": False, "error": "article not found", "url": url}
            for k, v in fields.items():
                setattr(art, k, v)
            has_ai_update = bool(fields.get("ai_summary") or fields.get("title_zh") or fields.get("category"))
            ai_ready = bool((fields.get("ai_summary") or art.ai_summary) and (fields.get("title_zh") or art.title_zh or fields.get("category") or art.category))
            if has_ai_update and ai_ready:
                art.ai_status = "done"
                art.ai_retries = 0
            db.commit()
            return {"ok": True, "id": art.id, "url": url}
        finally:
            db.close()

    results = [_apply(it) for it in items]
    if isinstance(payload, dict):
        return results[0]
    return {"results": results}


@router.post("/digest")
def push_digest(
    request: Request,
    payload: Any,
    authorization: str = Header(default=""),
):
    import json as _json
    from datetime import datetime as _dt

    from ..models import Digest, SessionLocal

    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not _valid_token(token):
        raise HTTPException(status_code=401, detail="invalid token")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    date_str = str(payload.get("date") or _dt.now().strftime("%Y-%m-%d"))
    try:
        _dt.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")

    events = payload.get("top_events") or []
    clean_events = []
    for e in events[:8]:
        if not isinstance(e, dict) or not str(e.get("title", "")).strip():
            continue
        clean_events.append({
            "title": str(e.get("title"))[:200],
            "comment": str(e.get("comment", ""))[:200],
            "url": str(e.get("url", ""))[:1000],
        })
    highlights_raw = payload.get("highlights") or {}
    clean_highlights = {}
    if isinstance(highlights_raw, dict):
        for cat, items in list(highlights_raw.items())[:6]:
            if isinstance(items, list) and items:
                clean_highlights[str(cat)[:30]] = [str(i)[:200] for i in items[:5]]
    trend = str(payload.get("trend", ""))[:800]
    if not clean_events and not clean_highlights and not trend:
        raise HTTPException(status_code=422, detail="digest content is empty")

    content = _json.dumps(
        {"top_events": clean_events, "highlights": clean_highlights, "trend": trend},
        ensure_ascii=False,
    )
    db = SessionLocal()
    try:
        row = db.query(Digest).filter(Digest.date == date_str).first()
        if row:
            row.content = content
        else:
            db.add(Digest(date=date_str, content=content))
        db.commit()
    finally:
        db.close()
    return {"ok": True, "date": date_str}
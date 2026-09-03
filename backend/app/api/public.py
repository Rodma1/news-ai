from __future__ import annotations

import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..models import Article, Digest, Guestbook, SessionLocal, Source

router = APIRouter(prefix="/api", tags=["public"])

_ASK_HITS: dict[str, list[float]] = {}
ASK_WINDOW = 60.0
ASK_MAX_PER_WINDOW = 5


def article_to_dict(a: Article) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "title_zh": a.title_zh,
        "url": a.url,
        "source": a.source,
        "source_type": a.source_type,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "fetched_at": a.fetched_at.isoformat() if a.fetched_at else None,
        "summary": a.ai_summary or a.summary_raw,
        "summary_raw": a.summary_raw,
        "content": a.content or "",
        "ai_ready": a.ai_status == "done",
        "category": a.category,
        "tags": [t for t in (a.tags or "").split(",") if t],
        "score": a.score,
        "entities": [e for e in (a.entities or "").split(",") if e],
    }


def digest_to_dict(d: Digest) -> dict:
    import json

    try:
        content = json.loads(d.content)
    except (ValueError, TypeError):
        content = {}
    return {"date": d.date, **content, "created_at": d.created_at.isoformat() if d.created_at else None}


@router.get("/articles")
def list_articles(
    sort: str = "time",
    page: int = 1,
    page_size: int = 20,
    tag: str = "",
    source: str = "",
    category: str = "",
    q: str = "",
    date: str = "",
    status: str = "",
):
    db = SessionLocal()
    try:
        query = db.query(Article)
        if source:
            query = query.filter(Article.source == source)
        if category:
            query = query.filter(Article.category == category)
        if status in ("pending", "done", "failed"):
            query = query.filter(Article.ai_status == status)
        if tag:
            query = query.filter(Article.tags.contains(tag))
        if q:

            query = query.filter(
                Article.title.contains(q)
                | Article.ai_summary.contains(q)
                | Article.summary_raw.contains(q)
                | Article.tags.contains(q)
                | Article.category.contains(q)
            )
        if date:
            try:
                day = datetime.strptime(date, "%Y-%m-%d")
                query = query.filter(Article.fetched_at >= day, Article.fetched_at < day + timedelta(days=1))
            except ValueError:
                pass
        if sort == "score":
            query = query.order_by(Article.score.desc().nullslast(), Article.fetched_at.desc())
        else:
            query = query.order_by(Article.fetched_at.desc())
        total = query.count()
        items = query.offset(max(0, page - 1) * page_size).limit(min(page_size, 100)).all()
        return {"total": total, "page": page, "page_size": page_size, "items": [article_to_dict(a) for a in items]}
    finally:
        db.close()


@router.get("/articles/{article_id}")
def get_article(article_id: int):
    db = SessionLocal()
    try:
        art = db.get(Article, article_id)
        if art is None:
            raise HTTPException(status_code=404, detail="not found")
        return article_to_dict(art)
    finally:
        db.close()


@router.get("/digests")
def list_digests():
    db = SessionLocal()
    try:
        rows = db.query(Digest).order_by(Digest.date.desc()).limit(60).all()
        return {"items": [{"date": d.date} for d in rows]}
    finally:
        db.close()


@router.get("/digests/latest")
def latest_digest():
    db = SessionLocal()
    try:
        d = db.query(Digest).order_by(Digest.date.desc()).first()
        return digest_to_dict(d) if d else {"date": None}
    finally:
        db.close()


@router.get("/digests/{date}")
def get_digest(date: str):
    db = SessionLocal()
    try:
        d = db.query(Digest).filter(Digest.date == date).first()
        return digest_to_dict(d) if d else {"error": "not found"}
    finally:
        db.close()


@router.get("/sources")
def list_sources():
    db = SessionLocal()
    try:
        names = [row[0] for row in db.query(Article.source).distinct().all() if row[0]]
        return {"sources": sorted(names)}
    finally:
        db.close()


class AskRequest(BaseModel):
    question: str


def _check_ask_rate(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    hits = [t for t in _ASK_HITS.get(ip, []) if now - t < ASK_WINDOW]
    if len(hits) >= ASK_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="提问太频繁，请稍后再试")
    hits.append(now)
    _ASK_HITS[ip] = hits
    if len(_ASK_HITS) > 1000:
        cutoff = now - ASK_WINDOW
        for k in list(_ASK_HITS.keys()):
            _ASK_HITS[k] = [t for t in _ASK_HITS[k] if t > cutoff]
            if not _ASK_HITS[k]:
                del _ASK_HITS[k]


@router.post("/ask")
def ask(req: AskRequest, request: Request):
    _check_ask_rate(request)
    from ..services.ai import answer_question

    question = req.question.strip()
    if not question:
        return {"answer": "请输入你的问题", "sources": []}
    return answer_question(question[:500])


@router.get("/stats")
def stats():
    db = SessionLocal()
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            "total": db.query(Article).count(),
            "today": db.query(Article).filter(Article.fetched_at >= today).count(),
            "digests": db.query(Digest).count(),
        }
    finally:
        db.close()


_GB_HITS: dict[str, float] = {}
GB_COOLDOWN = 60.0


class GuestbookRequest(BaseModel):
    name: str = ""
    contact: str = ""
    content: str


@router.post("/guestbook")
def add_guestbook(req: GuestbookRequest, request: Request):

    ip = request.client.host if request.client else "unknown"
    now = time.time()
    if now - _GB_HITS.get(ip, 0) < GB_COOLDOWN:
        raise HTTPException(status_code=429, detail="留言太频繁，请 1 分钟后再试")
    content = req.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="留言内容不能为空")
    _GB_HITS[ip] = now
    db = SessionLocal()
    try:
        db.add(Guestbook(
            name=(req.name.strip() or "匿名")[:20],
            contact=req.contact.strip()[:100],
            content=content[:500],
            ip=ip[:60],
        ))
        db.commit()
        return {"ok": True}
    finally:
        db.close()
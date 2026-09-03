from __future__ import annotations

import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel

from ..config import settings
from ..database import hash_password
from ..models import ApiToken, Article, Digest, SessionLocal, Source
from ..services import ai
from ..services.scheduler import run_crawl_and_process

router = APIRouter(prefix="/api/admin", tags=["admin"])

_sessions: dict[str, float] = {}
SESSION_TTL = 24 * 3600


def _token_from(request: Request, authorization: str) -> str:
    token = request.cookies.get("admin_token", "")
    if not token and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
    return token


def _auth(request: Request, authorization: str) -> str:
    token = _token_from(request, authorization)
    if not token or token not in _sessions:
        raise HTTPException(status_code=401, detail="unauthorized")
    if datetime.now().timestamp() - _sessions[token] > SESSION_TTL:
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="session expired")
    _sessions[token] = datetime.now().timestamp()
    return token


def is_logged_in(request: Request) -> bool:
    token = _token_from(request, "")
    return bool(token and token in _sessions)


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordRequest(BaseModel):
    old_password: str
    new_password: str


class SourceRequest(BaseModel):
    name: str
    url: str
    type: str = "rss"
    enabled: bool = True


class SettingsRequest(BaseModel):
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    crawl_times: Optional[str] = None
    digest_time: Optional[str] = None
    max_ai_per_day: Optional[int] = None


@router.post("/login")
def login(req: LoginRequest):
    from ..models import AdminUser

    db = SessionLocal()
    try:
        user = db.query(AdminUser).filter(AdminUser.username == req.username).first()
        if not user or hash_password(req.password, user.salt) != user.password_hash:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
    finally:
        db.close()
    token = secrets.token_hex(24)
    _sessions[token] = datetime.now().timestamp()
    return {"token": token, "username": req.username}


@router.put("/password")
def change_password(request: Request, req: PasswordRequest, authorization: str = Header(default="")):
    _auth(request, authorization)
    from ..models import AdminUser

    if len(req.new_password) < 6:
        raise HTTPException(status_code=422, detail="新密码至少6位")
    db = SessionLocal()
    try:
        user = db.query(AdminUser).first()
        if hash_password(req.old_password, user.salt) != user.password_hash:
            raise HTTPException(status_code=401, detail="旧密码错误")
        user.password_hash = hash_password(req.new_password, user.salt)
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.get("/stats")
def stats(request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        by_status = {status: db.query(Article).filter(Article.ai_status == status).count() for status in ["pending", "done", "failed"]}
        return {
            "total": db.query(Article).count(),
            "today": db.query(Article).filter(Article.fetched_at >= today).count(),
            "ai_status": by_status,
            "digests": db.query(Digest).count(),
            "sources": {
                "total": db.query(Source).count(),
                "enabled": db.query(Source).filter(Source.enabled.is_(True)).count(),
            },
        }
    finally:
        db.close()


@router.get("/sources")
def list_sources(request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        rows = db.query(Source).order_by(Source.id).all()
        return {"items": [
            {
                "id": s.id, "name": s.name, "url": s.url, "type": s.type,
                "enabled": s.enabled,
                "last_crawled_at": s.last_crawled_at.isoformat() if s.last_crawled_at else None,
                "last_status": s.last_status,
            } for s in rows
        ]}
    finally:
        db.close()


@router.post("/sources")
def create_source(request: Request, req: SourceRequest, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        src = Source(name=req.name, url=req.url, type=req.type, enabled=req.enabled)
        db.add(src)
        db.commit()
        return {"id": src.id}
    finally:
        db.close()


@router.put("/sources/{source_id}")
def update_source(source_id: int, request: Request, req: SourceRequest, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        src = db.get(Source, source_id)
        if not src:
            raise HTTPException(status_code=404, detail="not found")
        src.name = req.name
        src.url = req.url
        src.type = req.type
        src.enabled = req.enabled
        db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        src = db.get(Source, source_id)
        if src:
            db.delete(src)
            db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.get("/settings")
def get_settings_view(request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    api_key = ai.get_setting("llm_api_key", settings.LLM_API_KEY)
    return {
        "llm_base_url": ai.get_setting("llm_base_url", settings.LLM_BASE_URL),
        "llm_api_key": f"••••{api_key[-4:]}" if api_key else "",
        "llm_api_key_set": bool(api_key),
        "llm_model": ai.get_setting("llm_model", settings.LLM_MODEL),
        "crawl_times": ai.get_setting("crawl_times", ",".join(settings.CRAWL_TIMES)),
        "digest_time": ai.get_setting("digest_time", settings.DIGEST_TIME),
        "max_ai_per_day": ai.get_setting("max_ai_per_day", str(settings.MAX_AI_PER_DAY)),
    }


@router.put("/settings")
def update_settings(request: Request, req: SettingsRequest, authorization: str = Header(default="")):
    _auth(request, authorization)
    if req.llm_base_url is not None:
        ai.set_setting("llm_base_url", req.llm_base_url.strip())
    if req.llm_api_key:
        ai.set_setting("llm_api_key", req.llm_api_key.strip())
    if req.llm_model is not None:
        ai.set_setting("llm_model", req.llm_model.strip())
    if req.crawl_times is not None:
        times = [t.strip() for t in req.crawl_times.split(",") if t.strip()]
        if times:
            ai.set_setting("crawl_times", ",".join(times))
    if req.digest_time is not None and req.digest_time.strip():
        ai.set_setting("digest_time", req.digest_time.strip())
    if req.max_ai_per_day is not None:
        ai.set_setting("max_ai_per_day", str(max(1, req.max_ai_per_day)))
    return {"ok": True}


@router.post("/crawl")
def trigger_crawl(background_tasks: BackgroundTasks, request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    background_tasks.add_task(run_crawl_and_process)
    return {"ok": True, "message": "采集任务已开始"}


@router.post("/process")
def trigger_process(background_tasks: BackgroundTasks, request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    background_tasks.add_task(ai.process_pending)
    return {"ok": True, "message": "AI 处理任务已开始"}


@router.post("/digest")
def trigger_digest(background_tasks: BackgroundTasks, request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    background_tasks.add_task(ai.generate_digest, None)
    return {"ok": True, "message": "日报生成任务已开始"}


@router.get("/tokens")
def list_tokens(request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        rows = db.query(ApiToken).order_by(ApiToken.id.desc()).all()
        return {"items": [
            {"id": t.id, "token": t.token, "note": t.note, "created_at": t.created_at.isoformat() if t.created_at else None}
            for t in rows
        ]}
    finally:
        db.close()


class TokenRequest(BaseModel):
    note: str = ""


@router.post("/tokens")
def create_token(request: Request, req: TokenRequest, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        token = secrets.token_hex(20)
        row = ApiToken(token=token, note=req.note[:120])
        db.add(row)
        db.commit()
        return {"token": token}
    finally:
        db.close()


@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, request: Request, authorization: str = Header(default="")):
    _auth(request, authorization)
    db = SessionLocal()
    try:
        row = db.get(ApiToken, token_id)
        if row:
            db.delete(row)
            db.commit()
    finally:
        db.close()
    return {"ok": True}


@router.get("/views")
def view_stats(request: Request, days: int = 7, authorization: str = Header(default="")):
    _auth(request, authorization)
    days = max(1, min(days, 60))
    from datetime import timedelta

    from sqlalchemy import func

    from ..models import Article, PageView

    since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    db = SessionLocal()
    try:
        rows = (
            db.query(
                func.date(PageView.created_at).label("d"),
                func.count().label("pv"),
                func.count(func.distinct(PageView.ip)).label("uv"),
            )
            .filter(PageView.created_at >= since)
            .group_by("d")
            .order_by("d")
            .all()
        )
        by_day = {str(r.d): {"date": str(r.d), "pv": r.pv, "uv": r.uv} for r in rows}
        daily = []
        for i in range(days):
            d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
            daily.append(by_day.get(d, {"date": d, "pv": 0, "uv": 0}))
        hot_rows = (
            db.query(PageView.article_id, func.count().label("pv"))
            .filter(PageView.article_id.isnot(None))
            .group_by(PageView.article_id)
            .order_by(func.count().desc())
            .limit(10)
            .all()
        )
        hot = []
        for aid, pv in hot_rows:
            art = db.get(Article, aid)
            if art:
                hot.append({"id": aid, "title": art.title_zh or art.title, "pv": pv})
        total_pv = db.query(PageView).count()
        return {"days": daily, "hot": hot, "total_pv": total_pv}
    finally:
        db.close()

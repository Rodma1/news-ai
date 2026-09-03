from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import admin, ingest, public
from .api.admin import is_logged_in
from .config import settings
from .database import init_db
from .models import Article, Digest, Guestbook, PageView, SessionLocal
from .services.scheduler import scheduler_loop

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def fmt_time(value, fmt="%m-%d %H:%M"):
    if not value:
        return ""
    try:
        return value.strftime(fmt)
    except AttributeError:
        return str(value)


def time_label(value):
    if not value:
        return ""
    diff = (datetime.now() - value).total_seconds()
    if diff < 3600:
        return f"{max(1, int(diff // 60))} 分钟前"
    if diff < 86400:
        return f"{int(diff // 3600)} 小时前"
    return value.strftime("%m-%d")


templates.env.filters["fmt"] = fmt_time
templates.env.filters["timelabel"] = time_label

app = FastAPI(title="AI 资讯站", docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(public.router)
app.include_router(ingest.router)
app.include_router(admin.router)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.on_event("startup")
def startup():
    init_db()
    asyncio.create_task(scheduler_loop())


CATEGORIES = ["大模型", "多模态", "Agent", "开源", "硬件芯片", "政策监管", "论文", "产品应用", "行业动态"]


def _query_articles(db, sort, category, source, q, page, page_size=20):
    query = db.query(Article)
    if source:
        query = query.filter(Article.source == source)
    if category:
        query = query.filter(Article.category == category)
    if q:
        query = query.filter(
            Article.title.contains(q)
            | Article.ai_summary.contains(q)
            | Article.summary_raw.contains(q)
            | Article.tags.contains(q)
            | Article.category.contains(q)
        )
    if sort == "score":
        query = query.order_by(Article.score.desc().nullslast(), Article.fetched_at.desc())
    else:
        query = query.order_by(Article.fetched_at.desc())
    total = query.count()
    items = query.offset(max(0, page - 1) * page_size).limit(page_size).all()
    return total, items


def _latest_digest(db):
    d = db.query(Digest).order_by(Digest.date.desc()).first()
    if not d:
        return None
    try:
        content = json.loads(d.content)
    except (ValueError, TypeError):
        content = {}
    return {"date": d.date, **content}


def _hot_this_week(db, limit=5):
    since = datetime.now() - timedelta(days=7)
    rows = (
        db.query(Article)
        .filter(Article.fetched_at >= since)
        .filter(Article.score >= 4)
        .order_by(Article.score.desc(), Article.fetched_at.desc())
        .limit(limit)
        .all()
    )
    out = []
    for a in rows:
        d = public.article_to_dict(a)
        d["time_label"] = time_label(a.fetched_at)
        out.append(d)
    return out


BOT_UA = ("bot", "spider", "crawler", "slurp", "curl", "wget", "python-requests", "headless")


def _client_ip(request: Request) -> str:
    ip = request.headers.get("x-real-ip") or ""
    if not ip:
        fwd = request.headers.get("x-forwarded-for") or ""
        ip = fwd.split(",")[0].strip()
    return (ip or (request.client.host if request.client else "unknown"))[:60]


def _track(request: Request, path: str, article_id: int | None = None) -> None:
    ua = (request.headers.get("user-agent") or "").lower()
    if any(b in ua for b in BOT_UA):
        return
    db = SessionLocal()
    try:
        db.add(PageView(
            path=path[:250],
            article_id=article_id,
            ip=_client_ip(request),
        ))
        db.commit()
    except Exception:
        pass
    finally:
        db.close()


@app.get("/")
def index(request: Request, sort: str = "time", category: str = "", source: str = "", q: str = "", page: int = 1):
    _track(request, "/")
    db = SessionLocal()
    try:
        total, arts = _query_articles(db, sort, category, source, q, page)
        sources = sorted({row[0] for row in db.query(Article.source).distinct().all() if row[0]})
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        stats = {
            "today": db.query(Article).filter(Article.fetched_at >= today).count(),
            "total": db.query(Article).count(),
        }
        digest = _latest_digest(db)
        is_first_page = page == 1 and not category and not source and not q and sort == "time"
        hot_week = _hot_this_week(db) if is_first_page else []
        articles = []
        for a in arts:
            d = public.article_to_dict(a)
            d["time_label"] = time_label(a.fetched_at)
            articles.append(d)
        return templates.TemplateResponse(request, "index.html", {
            "request": request,
            "articles": articles,
            "total": total,
            "page": page,
            "pages": max(1, (total + 19) // 20),
            "sort": sort,
            "category": category,
            "source": source,
            "q": q,
            "categories": CATEGORIES,
            "sources": sources,
            "stats": stats,
            "digest": digest,
            "hot_week": hot_week,
        })
    finally:
        db.close()


def _related_articles(db, art: Article, limit: int = 5) -> list[Article]:
    query = db.query(Article).filter(Article.id != art.id)
    related: list[Article] = []
    if art.category:
        related = (
            query.filter(Article.category == art.category, Article.ai_status == "done")
            .order_by(Article.score.desc().nullslast(), Article.fetched_at.desc())
            .limit(limit)
            .all()
        )
    if len(related) < limit:
        exclude_ids = {art.id} | {r.id for r in related}
        fill = (
            query.filter(~Article.id.in_(exclude_ids))
            .order_by(Article.fetched_at.desc())
            .limit(limit - len(related))
            .all()
        )
        related.extend(fill)
    return related[:limit]


@app.get("/article/{article_id}")
def article_page(article_id: int, request: Request):
    db = SessionLocal()
    try:
        art = db.get(Article, article_id)
        if not art:
            return RedirectResponse("/", status_code=302)
        _track(request, f"/article/{article_id}", article_id)
        a = public.article_to_dict(art)
        related = [
            {"id": r.id, "title": r.title_zh or r.title, "category": r.category, "time_label": time_label(r.fetched_at)}
            for r in _related_articles(db, art)
        ]
        return templates.TemplateResponse(request, "article.html", {
            "request": request,
            "a": a,
            "published": fmt_time(art.published_at or art.fetched_at, "%Y-%m-%d %H:%M"),
            "related": related,
        })
    finally:
        db.close()


@app.get("/digest")
def digest_page(request: Request, date: str = ""):
    _track(request, "/digest")
    db = SessionLocal()
    try:
        rows = db.query(Digest).order_by(Digest.date.desc()).limit(60).all()
        dates = [r.date for r in rows]
        target = date if date in dates else (dates[0] if dates else "")
        current = None
        if target:
            row = db.query(Digest).filter(Digest.date == target).first()
            try:
                content = json.loads(row.content)
            except (ValueError, TypeError):
                content = {}
            current = {"date": target, **content}
        return templates.TemplateResponse(request, "digest.html", {
            "request": request,
            "dates": dates,
            "current": current,
        })
    finally:
        db.close()


@app.get("/ask")
def ask_page(request: Request):
    _track(request, "/ask")
    return templates.TemplateResponse(request, "ask.html", {"request": request})


@app.get("/about")
def about_page(request: Request):
    _track(request, "/about")
    return templates.TemplateResponse(request, "about.html", {"request": request})


@app.get("/guestbook")
def guestbook_page(request: Request, page: int = 1):
    _track(request, "/guestbook")
    db = SessionLocal()
    try:
        page_size = 20
        query = db.query(Guestbook).order_by(Guestbook.created_at.desc())
        total = query.count()
        msgs = query.offset(max(0, page - 1) * page_size).limit(page_size).all()
        return templates.TemplateResponse(request, "guestbook.html", {
            "request": request,
            "messages": msgs,
            "page": page,
            "pages": max(1, (total + page_size - 1) // page_size),
            "total": total,
        })
    finally:
        db.close()


@app.get("/tag/{tag_name}")
def tag_page(tag_name: str, request: Request, page: int = 1):
    _track(request, f"/tag/{tag_name}")
    db = SessionLocal()
    try:
        tag_name = tag_name.strip()[:50]
        page_size = 20
        query = (
            db.query(Article)
            .filter(Article.tags.contains(tag_name))
            .order_by(Article.fetched_at.desc())
        )
        total = query.count()
        arts = query.offset(max(0, page - 1) * page_size).limit(page_size).all()
        articles = []
        for a in arts:
            d = public.article_to_dict(a)
            d["time_label"] = time_label(a.fetched_at)
            articles.append(d)
        return templates.TemplateResponse(request, "tag.html", {
            "request": request,
            "tag_name": tag_name,
            "articles": articles,
            "total": total,
            "page": page,
            "pages": max(1, (total + page_size - 1) // page_size),
        })
    finally:
        db.close()


ADMIN_PAGES = {"dashboard", "articles", "guestbook", "sources", "settings", "tokens"}


@app.get("/admin")
def admin_root(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/admin/dashboard", status_code=302)
    return RedirectResponse("/admin/login", status_code=302)


@app.get("/admin/login")
def admin_login_page(request: Request):
    if is_logged_in(request):
        return RedirectResponse("/admin/dashboard", status_code=302)
    return templates.TemplateResponse(request, "admin/login.html", {"request": request})


@app.get("/admin/{page}")
def admin_page(page: str, request: Request):
    if page not in ADMIN_PAGES:
        return RedirectResponse("/admin", status_code=302)
    if not is_logged_in(request):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse(request, f"admin/{page}.html", {"request": request, "page": page})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(content=b"", media_type="image/x-icon")


@app.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base = str(request.base_url).rstrip("/")
    body = f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\n\nSitemap: {base}/sitemap.xml\n"
    return PlainTextResponse(body, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    db = SessionLocal()
    try:
        base = str(request.base_url).rstrip("/")
        rows = db.query(Article.id, Article.fetched_at).order_by(Article.fetched_at.desc()).limit(2000).all()
        urls = [f"<url><loc>{base}/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>",
                f"<url><loc>{base}/digest</loc><changefreq>daily</changefreq><priority>0.8</priority></url>",
                f"<url><loc>{base}/ask</loc><changefreq>weekly</changefreq><priority>0.5</priority></url>"]
        for aid, ts in rows:
            lastmod = (ts or datetime.now()).strftime("%Y-%m-%d")
            urls.append(f"<url><loc>{base}/article/{aid}</loc><lastmod>{lastmod}</lastmod><priority>0.6</priority></url>")
        xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(urls) + "\n</urlset>"
        return Response(content=xml, media_type="application/xml")
    finally:
        db.close()


@app.get("/rss", include_in_schema=False)
def rss(request: Request):
    db = SessionLocal()
    try:
        base = str(request.base_url).rstrip("/")
        arts = (
            db.query(Article)
            .order_by(Article.fetched_at.desc())
            .limit(50)
            .all()
        )
        items = []
        for a in arts:
            title = a.title_zh or a.title
            desc = (a.ai_summary or a.summary_raw or "")[:300]
            pub = (a.published_at or a.fetched_at or datetime.now()).astimezone()
            pub_rfc = format_datetime(pub)
            cats = f"<category>{escape(a.category)}</category>" if a.category else ""
            items.append(
                f"<item>"
                f"<title>{escape(title)}</title>"
                f"<link>{escape(base)}/article/{a.id}</link>"
                f"<guid isPermaLink=\"true\">{escape(base)}/article/{a.id}</guid>"
                f"<description>{escape(desc)}</description>"
                f"{cats}"
                f"<source url=\"{escape(a.url)}\">{escape(a.source)}</source>"
                f"<pubDate>{pub_rfc}</pubDate>"
                f"</item>"
            )
        build_date = format_datetime(datetime.now().astimezone())
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
            f"<title>AI 资讯站</title>"
            f"<link>{escape(base)}</link>"
            "<description>每日精选 AI 资讯，大模型生成中文摘要、评分与日报</description>"
            "<language>zh-CN</language>"
            f"<lastBuildDate>{build_date}</lastBuildDate>"
            f"<atom:link href=\"{escape(base)}/rss\" rel=\"self\" type=\"application/rss+xml\" />"
            + "".join(items)
            + "\n</channel>\n</rss>"
        )
        return Response(content=xml, media_type="application/rss+xml")
    finally:
        db.close()

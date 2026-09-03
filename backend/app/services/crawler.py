from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ..http_client import curl_get
from ..models import Article, SessionLocal, Source

TRACKING_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "igshid", "ref", "referer", "from",
}


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        query = [
            (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in TRACKING_KEYS
        ]
        return urlunparse(
            (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", urlencode(query), "")
        )
    except ValueError:
        return url


def parse_date(value: str):
    if not value:
        return None
    value = value.strip()
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').strip()


def parse_feed(data: bytes) -> list[dict]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = ""
        link = ""
        published = ""
        summary = ""
        for child in el:
            tag = _local(child.tag)
            if tag == "title" and not title:
                title = (child.text or "").strip()
            elif tag == "link":
                link = child.get("href") or (child.text or "").strip() or link
            elif tag in ("pubdate", "published", "updated", "date") and not published:
                published = (child.text or "").strip()
            elif tag in ("description", "summary", "encoded", "content") and not summary:
                summary = (child.text or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "published": published, "summary": summary})
    return items


def _extract_ai_fields(it: dict) -> tuple[str, str, str, int | None]:
    from .ai import CATEGORIES

    ai_summary = _strip_html(str(it.get("ai_summary") or ""))[:300]
    title_zh = _strip_html(str(it.get("title_zh") or ""))[:500]
    category = str(it.get("category") or "")
    category = category if category in CATEGORIES else ""
    try:
        score = max(1, min(5, int(it.get("score") or 0))) if it.get("score") is not None else None
    except (TypeError, ValueError):
        score = None
    return title_zh, ai_summary, category, score


def insert_articles(items: list[dict], source_name: str = "", source_type: str = "push") -> tuple[int, int, int]:
    inserted = duplicated = updated = 0
    db = SessionLocal()
    try:
        for it in items:
            title = _strip_html(str(it.get("title", "")))[:500]
            url = normalize_url(str(it.get("url", "")))
            if not title or not url.startswith("http"):
                continue
            title_zh, ai_summary, category, score = _extract_ai_fields(it)
            ai_ready = bool(ai_summary and (title_zh or category))
            existing = (
                db.query(Article)
                .filter((Article.url == url) | (Article.title == title))
                .first()
            )
            if existing:
                if existing.ai_status != "done" and ai_ready:
                    existing.title_zh = title_zh or existing.title_zh
                    existing.ai_summary = ai_summary or existing.ai_summary
                    existing.category = category or existing.category
                    existing.score = score if score is not None else existing.score
                    if it.get("entities"):
                        existing.entities = ",".join(str(e) for e in it.get("entities")[:5])[:250]
                    existing.ai_status = "done"
                    updated += 1
                else:
                    duplicated += 1
                continue
            db.add(Article(
                title=title,
                url=url[:1000],
                source=str(it.get("source") or source_name)[:120],
                source_type=str(it.get("source_type") or source_type),
                published_at=it.get("published_at") or parse_date(str(it.get("published") or "")),
                summary_raw=_strip_html(str(it.get("summary") or ""))[:2000],
                content=_strip_html(str(it.get("content") or ""))[:50000],
                tags=",".join(it.get("tags") or [])[:250],
                title_zh=title_zh or None,
                ai_summary=ai_summary or None,
                category=category or None,
                score=score,
                entities=",".join(str(e) for e in (it.get("entities") or [])[:5])[:250],
                ai_status="done" if ai_ready else "pending",
            ))
            inserted += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return inserted, duplicated, updated


def _update_source_status(source_id: int, status: str) -> None:
    db = SessionLocal()
    try:
        src = db.get(Source, source_id)
        if src:
            src.last_crawled_at = datetime.now()
            src.last_status = status
            db.commit()
    finally:
        db.close()


def crawl_all() -> dict:
    db = SessionLocal()
    sources = db.query(Source).filter(Source.enabled.is_(True)).all()
    db.close()
    stats = {"sources": len(sources), "ok": 0, "failed": 0, "inserted": 0, "duplicates": 0}
    for src in sources:
        try:
            status, data = curl_get(src.url, timeout=30)
            items = parse_feed(data) if status == 200 else []
            ins, dup, _upd = insert_articles(items, src.name, src.type)
            stats["ok"] += 1
            stats["inserted"] += ins
            stats["duplicates"] += dup
            _update_source_status(src.id, "ok")
        except Exception:
            stats["failed"] += 1
            _update_source_status(src.id, "failed")
    return stats
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from ..config import settings
from ..http_client import curl_post_json
from ..models import Article, Digest, SessionLocal, Setting

CATEGORIES = ["大模型", "多模态", "Agent", "开源", "硬件芯片", "政策监管", "论文", "产品应用", "行业动态"]


def _get_setting(db, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    if row is None or row.value == "" or row.value is None:
        return default
    return row.value


def set_setting(key: str, value: str) -> None:
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


def get_setting(key: str, default: str = "") -> str:
    db = SessionLocal()
    try:
        return _get_setting(db, key, default)
    finally:
        db.close()


def llm_config() -> dict:
    return {
        "base_url": get_setting("llm_base_url", settings.LLM_BASE_URL).rstrip("/"),
        "api_key": get_setting("llm_api_key", settings.LLM_API_KEY),
        "model": get_setting("llm_model", settings.LLM_MODEL),
    }


def llm_chat(cfg: dict, prompt: str, system: str = "你是一名专业的 AI 资讯编辑。") -> str | None:
    if not cfg.get("api_key"):
        return None
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    status, data = curl_post_json(
        f"{cfg['base_url']}/chat/completions",
        payload,
        timeout=90,
        headers={"Authorization": f"Bearer {cfg['api_key']}"},
    )
    if status != 200:
        return None
    try:
        return json.loads(data)["choices"][0]["message"]["content"]
    except Exception:
        return None


def _parse_json(content: str | None) -> dict | None:
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def enrich_article(db, art: Article, cfg: dict) -> bool:
    prompt = (
        "根据以下文章信息，输出 JSON 对象，字段要求：\n"
        "title_zh: 标题的简体中文翻译（如果标题本来就是中文，与原标题相同）\n"
        'summary: 不超过120字的中文摘要\n'
        f'category: 从这些分类中选一个：{"、".join(CATEGORIES)}\n'
        "tags: 2-4个中文标签（数组）\n"
        "score: 该文章对关注AI的人的重要性评分，1-5 整数，5 最重要\n"
        "entities: 涉及的公司、机构或模型名（数组，可为空）\n\n"
        f"标题：{art.title}\n"
        f"来源：{art.source}\n"
        f"原文摘要：{art.summary_raw[:800]}\n"
        f"正文内容：{(art.content or '')[:1200]}"
    )
    content = llm_chat(cfg, prompt)
    result = _parse_json(content)
    if not result:
        return False
    art.title_zh = str(result.get("title_zh", ""))[:500] or None
    art.ai_summary = str(result.get("summary", ""))[:300]
    category = str(result.get("category", ""))
    art.category = category if category in CATEGORIES else "行业动态"
    tags = result.get("tags") or []
    art.tags = ",".join(str(t) for t in tags[:5])[:250]
    try:
        art.score = max(1, min(5, int(result.get("score", 3))))
    except (TypeError, ValueError):
        art.score = 3
    entities = result.get("entities") or []
    art.entities = ",".join(str(e) for e in entities[:5])[:250]
    art.ai_status = "done"
    return True


def _today_ai_count() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    if get_setting("ai_count_date") != today:
        set_setting("ai_count_date", today)
        set_setting("ai_count", "0")
    try:
        return int(get_setting("ai_count", "0"))
    except ValueError:
        return 0


def process_pending(limit: int | None = None) -> dict:
    cfg = llm_config()
    max_per_day = int(get_setting("max_ai_per_day", str(settings.MAX_AI_PER_DAY)))
    budget = max_per_day - _today_ai_count()
    if budget <= 0:
        return {"processed": 0, "ok": 0, "failed": 0, "reason": "daily limit reached"}
    limit = min(limit or budget, budget)
    db = SessionLocal()
    processed = ok = failed = 0
    try:
        while processed < limit:
            art = (
                db.query(Article)
                .filter(Article.ai_status == "pending", Article.ai_retries < 3)
                .order_by(Article.fetched_at.desc())
                .first()
            )
            if art is None:
                break
            processed += 1
            if enrich_article(db, art, cfg):
                ok += 1
            else:
                art.ai_retries += 1
                if art.ai_retries >= 3:
                    art.ai_status = "failed"
                failed += 1
            db.commit()
        if processed:
            set_setting("ai_count", str(_today_ai_count() + processed))
    finally:
        db.close()
    return {"processed": processed, "ok": ok, "failed": failed}


def generate_digest(date_str: str | None = None) -> dict | None:
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    day_start = datetime.strptime(date_str, "%Y-%m-%d")
    day_end = day_start + timedelta(days=1)
    db = SessionLocal()
    try:
        existing = db.query(Digest).filter(Digest.date == date_str).first()
        if existing:
            try:
                return json.loads(existing.content)
            except (ValueError, TypeError):
                pass
        arts = (
            db.query(Article)
            .filter(
                Article.fetched_at >= day_start,
                Article.fetched_at < day_end,
                Article.ai_status == "done",
            )
            .order_by(Article.score.desc(), Article.fetched_at.desc())
            .limit(30)
            .all()
        )
        if not arts:
            return None
        cfg = llm_config()
        lines = [
            f"{i + 1}. {a.title}（来源:{a.source} 分类:{a.category} 评分:{a.score}）\n摘要:{a.ai_summary}"
            for i, a in enumerate(arts)
        ]
        content = None
        if cfg.get("api_key"):
            prompt = (
                "以下是今天的 AI 资讯列表。请输出 JSON 对象：\n"
                'top_events: 最重要的5条事件，每条 {"title": "事件标题", "comment": "一句话中文点评", "url": "原文url"}\n'
                "highlights: 按分类的要点速览，{\"分类名\": [\"要点标题\", ...]}，最多4个分类，每类最多3条\n"
                "trend: 不超过300字的今日趋势中文总结\n\n"
                + "\n\n".join(lines)
            )
            content = _parse_json(llm_chat(cfg, prompt))
        if not content:
            content = {
                "top_events": [
                    {"title": a.title, "comment": (a.ai_summary or "")[:80], "url": a.url}
                    for a in arts[:5]
                ],
                "highlights": {},
                "trend": "",
            }

        digest = db.query(Digest).filter(Digest.date == date_str).first()
        payload = json.dumps(content, ensure_ascii=False)
        if digest:
            digest.content = payload
        else:
            digest = Digest(date=date_str, content=payload)
            db.add(digest)
        db.commit()
        return content
    finally:
        db.close()


STOPWORDS = {"哪些", "什么", "怎么", "如何", "请问", "帮我", "一下", "有没有", "这个", "那个", "还是", "以及"}


def _extract_keywords(question: str, max_kw: int = 8) -> list[str]:
    cleaned = re.sub("[，。？！、：；\u201c\u201d\u2018\u2019（）【】\\s]+", " ", question)
    words = [w for w in cleaned.split() if len(w) >= 2 and w not in STOPWORDS]
    kws: list[str] = []
    for w in words:
        if len(w) <= 4:
            kws.append(w)
        else:
            for i in range(len(w) - 1):
                gram = w[i:i + 2]
                if gram not in STOPWORDS:
                    kws.append(gram)
    result = []
    for k in kws:
        if k not in result:
            result.append(k)
    return result[:max_kw]


def answer_question(question: str) -> dict:
    db = SessionLocal()
    try:
        kws = _extract_keywords(question)
        pool = (
            db.query(Article)
            .filter(Article.ai_status == "done")
            .order_by(Article.fetched_at.desc())
            .limit(500)
            .all()
        )
        scored = []
        for a in pool:
            text = f"{a.title} {a.ai_summary or ''} {a.tags or ''} {a.category or ''} {a.entities or ''}"
            hits = sum(1 for k in kws if k in text)
            if hits:
                scored.append((hits, a.score or 0, a))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        selected = [a for _, _, a in scored[:15]]
        sources = [
            {"id": a.id, "title": a.title, "url": a.url, "source": a.source}
            for a in selected
        ]
        cfg = llm_config()
        if not cfg.get("api_key"):
            return {"answer": "（未配置大模型 API Key，以下为检索到的相关文章）", "sources": sources}
        context = "\n\n".join(
            f"[{i + 1}] {a.title}（来源:{a.source} 时间:{a.fetched_at.strftime('%m-%d')}）\n{a.ai_summary or a.summary_raw[:200]}"
            for i, a in enumerate(selected)
        )
        prompt = (
            "基于以下已采集的 AI 资讯回答用户问题。要求：用中文，简洁准确；"
            "引用信息时用 [编号] 标注来源；若资料不足以回答，请如实说明。\n\n"
            f"资讯资料：\n{context}\n\n用户问题：{question}"
        )
        payload = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": "你是一名 AI 资讯助手，只依据提供的资料回答问题。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
        status, data = curl_post_json(
            f"{cfg['base_url']}/chat/completions",
            payload,
            timeout=90,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
        )
        answer = ""
        if status == 200:
            try:
                answer = json.loads(data)["choices"][0]["message"]["content"]
            except Exception:
                answer = ""
        return {"answer": answer or "抱歉，回答生成失败，请稍后重试。", "sources": sources}
    finally:
        db.close()
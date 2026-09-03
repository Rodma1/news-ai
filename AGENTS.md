# AGENTS.md — AI 资讯站 项目说明

> 本文件供 AI 编码助手快速了解本项目。读完即可上手改代码、调接口、部署。

## 1. 项目概述

一个**前后端不分离**的 AI 资讯聚合站：定时从 RSS 源采集 AI 文章，大模型生成中文摘要/翻译/分类/评分，每晚整合成日报；同时开放推送接口供外部采集代理（如 WorkBuddy）写入已整理好的文章。单进程、SQLite、无前端工具链，目标是好维护、能跑在低配服务器（1.7G 内存 CentOS 7）。

## 2. 技术栈

- **后端**：FastAPI + Uvicorn + SQLAlchemy 2.x + SQLite
- **模板**：Jinja2（SSR，前后端不分离）
- **样式**：纯 CSS（明亮/暗黑双主题，CSS 变量）
- **出站 HTTP**：`subprocess + curl` 兜底（服务器 Python 可能缺 `_ssl` 模块）
- **Python**：3.11
- **无** Node / Vue / React / ORM 迁移工具 / Celery

## 3. 目录结构

```
news-ai/
├── AGENTS.md                       # 本文件
├── README.md                       # 部署手册
├── .gitignore
├── output/prd-ai-news-aggregator.md  # PRD
└── backend/
    ├── requirements.txt            # fastapi uvicorn sqlalchemy jinja2
    ├── .env / .env.example         # 配置（.env 不入 git）
    ├── run.py                      # 入口：uvicorn app.main:app
    ├── docs/push-api.md            # 推送接口对接文档（给外部采集代理）
    ├── data/                       # 运行时：news.db / backups / app.log（不入 git）
    ├── venv/                       # 虚拟环境（不入 git）
    └── app/
        ├── main.py                 # FastAPI 应用 + 页面路由 + cookie 鉴权 + 打点
        ├── config.py               # .env 加载 + Settings
        ├── models.py               # SQLAlchemy 模型
        ├── database.py             # 建表 + 轻量迁移 + 默认数据
        ├── http_client.py          # curl_get / curl_post_json（适配无 _ssl）
        ├── api/
        │   ├── public.py           # 公开 JSON 接口 + 留言提交 + 问答
        │   ├── ingest.py           # 推送接口（文章/日报/更新）+ Bearer 鉴权 + 限频
        │   └── admin.py            # 后台 API（cookie/header 鉴权）
        ├── services/
        │   ├── crawler.py          # RSS 解析 + 去重 + 入库 + AI 字段补齐
        │   ├── ai.py               # LLM 调用 + 摘要/翻译/评分/日报/问答/关键词检索
        │   └── scheduler.py        # 定时采集与日报循环
        ├── templates/              # Jinja2 模板
        │   ├── base.html           # 布局 + 主题切换 + OG meta + RSS 自动发现
        │   ├── index.html article.html digest.html ask.html about.html guestbook.html tag.html
        │   └── admin/{base,shell,login,dashboard,articles,guestbook,sources,settings,tokens}.html
        └── static/css/main.css     # 全量样式（含 [data-theme="dark"] 覆盖）
```

## 4. 数据模型（models.py）

| 表 | 说明 | 关键字段 |
|----|------|----------|
| `articles` | 文章 | title, title_zh(中文标题), url(unique), source, content(正文), summary_raw, ai_summary, category, tags(逗号分隔), score(1-5), entities, ai_status(pending/done/failed), ai_retries |
| `digests` | 日报 | date(unique, YYYY-MM-DD), content(JSON 文本) |
| `sources` | RSS 信息源 | name, url, type, enabled, last_crawled_at, last_status |
| `admin_users` | 后台账号 | username, password_hash, salt |
| `api_tokens` | 推送 Token | token(unique), note |
| `settings` | 键值配置（LLM 配置/采集时间/AI 计数） | key, value |
| `guestbook` | 留言 | name, contact, content, ip, created_at |
| `page_views` | 浏览记录 | path, article_id, ip, created_at |

**轻量迁移**：`database.py` 用 `PRAGMA table_info` + `ALTER TABLE` 手动加列（title_zh、content），新表靠 `create_all` 自动建。**改模型加列时需在 database.py 补 ALTER 逻辑**，没有 Alembic。

## 5. 接口清单

### 公开接口（无需鉴权，api/public.py）
- `GET /api/articles` — 文章列表，支持 sort/page/page_size/tag/source/category/q/date/**status**(pending/done/failed)
- `GET /api/articles/{id}` — 文章详情（不存在返回 404）
- `GET /api/digests` `/digests/latest` `/digests/{date}` — 日报
- `GET /api/sources` `/api/stats` — 来源/统计
- `POST /api/ask` — 问答（IP 限频 60 秒 5 次）
- `POST /api/guestbook` — 留言提交（IP 限频 60 秒 1 条，内容 ≤500 字）

### 推送接口（Bearer Token，api/ingest.py）
- `POST /api/v1/news` — 推送文章（单条或数组），支持 AI 字段（title_zh/ai_summary/category/score/entities），带齐即标记 done 不消耗站内 Key；重复 URL 且旧文未处理时自动补齐
- `POST /api/v1/news/update` — 按 url 更新已存在文章的字段（单条或数组，单项失败不中断）
- `POST /api/v1/digest` — 推送日报（覆盖同日）
- 限频：每 Token 60 秒 120 次

### 后台接口（cookie 或 Bearer session，api/admin.py）
- `POST /api/admin/login` `/put password` — 登录/改密
- `GET /api/admin/stats` `/views` — 仪表盘统计 / 浏览 PV/UV+热门
- `GET/POST/PUT/DELETE /api/admin/sources` — 信息源 CRUD
- `GET/PUT/DELETE /api/admin/articles` — 文章管理
- `GET/DELETE /api/admin/guestbook` — 留言管理
- `GET/PUT /api/admin/settings` — LLM 配置/采集时间
- `GET/POST/DELETE /api/admin/tokens` — Token 管理
- `POST /api/admin/crawl` `/process` `/digest` — 手动触发采集/AI/日报

### 页面路由（main.py）
`/` 首页 · `/article/{id}` 详情 · `/digest` 日报 · `/ask` 问答 · `/about` 关于 · `/guestbook` 留言 · `/tag/{tag}` 标签页 · `/admin/*` 后台 · `/rss` RSS · `/sitemap.xml` · `/robots.txt`

## 6. 核心流程

**采集 → AI → 日报**（scheduler.py，每 60 秒轮询）
1. 到达 `CRAWL_TIMES`（默认 07:00/12:00/20:00）→ `crawler.crawl_all()` 抓 RSS → `ai.process_pending()` 处理
2. 到达 `DIGEST_TIME`（默认 21:00）→ `ai.generate_digest()`；**当天已有日报则跳过**（外置优先）
3. AI 处理受 `MAX_AI_PER_DAY` 每日上限控制；设为 0 则站内完全不消耗 LLM Key

**外部推送**（WorkBuddy 等）
- 推送时自带 AI 字段 → 直接标记 done，站内零消耗
- 推送同 URL 但旧文 pending 且新数据带 AI 字段 → 自动补齐（修复 RSS 抢占）
- 完整闭环：`GET /api/articles?status=pending` 拉取 → 自己的模型整理 → `POST /api/v1/news/update` 写回

**浏览统计**（main.py `_track`）
- 6 个公开页面服务端打点，过滤爬虫 UA，优先读 `X-Real-IP`/`X-Forwarded-For`
- 后台仪表盘展示近 7 日 PV/UV 条形图 + 热门文章 Top10

## 7. 配置项（.env）

| 变量 | 默认 | 说明 |
|------|------|------|
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | admin / admin123 | 后台登录（务必改） |
| `INGEST_TOKEN` | change-me-please | 备用推送 Token |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | deepseek | 大模型配置 |
| `CRAWL_TIMES` | 07:00,12:00,20:00 | 采集时段（逗号分隔） |
| `DIGEST_TIME` | 21:00 | 日报生成时间 |
| `MAX_AI_PER_DAY` | 50 | 站内 AI 处理上限；**0 = 完全关闭站内 AI** |
| `PORT` | 8001 | 服务端口 |

LLM 配置也可在后台「设置」页改（存 settings 表，优先于 .env）。

## 8. 本地运行

```bash
cd backend
python3.11 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填入配置
python run.py          # http://localhost:8001
```

## 9. 部署要点

- 服务器：代码放 `/study/project/news-ai`，python3.11 在 `/study/env/python3.11/bin`
- venv 用 `/study/env/python3.11/bin/python3.11 -m venv venv` 创建
- nginx 反代到 127.0.0.1:8001，**必须设 `proxy_set_header X-Real-IP $remote_addr`**（否则 UV 失真）
- systemd 常驻，`WorkingDirectory=backend`，`ExecStart=venv/bin/python run.py`
- 详细命令见 README.md

## 10. 开发约定（改代码必读）

- **Jinja2 新签名**：`templates.TemplateResponse(request, "模板名", {...})`，旧签名会报 `TypeError: unhashable type: 'dict'`
- **SQLAlchemy 2.x**：裸字符串 SQL 必须用 `text()` 包裹（如 `db.execute(text("PRAGMA ..."))`），否则报 ArgumentError
- **单进程**：内存限频字典（`_ASK_HITS`/`_GB_HITS`/`_rate`/admin session）重启丢失，**不要部署多 worker**（uvicorn 单进程）
- **出站 HTTPS**：一律走 `http_client.curl_get/curl_post_json`，不要用 requests/httpx（服务器可能缺 `_ssl`）
- **CSS 变量主题**：改颜色改 `:root`，暗黑覆盖在 `[data-theme="dark"]`；硬编码 `rgba(15,23,42,...)` 背景需同步加 dark 覆盖
- **不加注释**除非必要；改完代码需验证（lint/跑服务）
- **分类枚举固定**：`大模型/多模态/Agent/开源/硬件芯片/政策监管/论文/产品应用/行业动态`（ai.py CATEGORIES），写入前校验

## 11. 已知坑

- **编辑器覆盖**：开发中多次出现编辑器旧标签页把磁盘文件覆盖回旧版本。改完文件若发现改动丢失，重读文件再重新应用编辑
- **沙箱 fork 限制**：本机 agent 沙箱进程数受限，无法跑 python/node 验证；验证需在用户终端执行
- **SQLite 并发**：单机个人站量级无问题；不要上多 worker，写锁竞争会报 `database is locked`
- **PageView 无限增长**：目前无自动清理，长期运行需手动清理或加定期任务（保留 90 天）

## 12. 外部采集代理对接

完整文档见 `backend/docs/push-api.md`。给外部代理（WorkBuddy/Hermes）只需提供：
1. Token（后台 `/admin/tokens` 生成）
2. `push-api.md` 文档 + BASE_URL（`https://域名`）
3. 建议它推送时自带 AI 字段（省站内 Key），每晚 20:30 前推送当日日报
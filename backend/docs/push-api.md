# 文章推送接口对接文档

> 本文档面向自动采集代理（WorkBuddy / Hermes 等定时任务或技能）。按本文约定推送，文章会自动入库、去重，并由大模型完成中文摘要、翻译、分类、打标签和评分。

## 1. 接口信息

| 项 | 值 |
|------|------|
| 地址 | `POST {BASE_URL}/api/v1/news` |
| 鉴权 | Header: `Authorization: Bearer <你的Token>` |
| Content-Type | `application/json` |
| Token 来源 | 网站后台 `/admin/tokens` → 「+ 生成新 Token」→ 点「显示」复制 |

BASE_URL：
- 本地调试：`http://localhost:8001`
- 服务器：`https://你的域名`

## 2. 请求体

支持单条对象或对象数组（推荐批量，一次任务拉取到的所有文章合成一个数组推送）。

```json
[
  {
    "title": "OpenAI 发布新一代推理模型",
    "url": "https://example.com/blog/oai-reasoning",
    "source": "WorkBuddy采集",
    "published_at": "2026-09-01T10:00:00",
    "summary": "OpenAI 今天发布了……（原文摘要或你抓取的正文开头）",
    "content": "完整正文文本（可选）……",
    "tags": ["OpenAI", "推理模型"]
  }
]
```

### 字段说明

| 字段 | 必填 | 类型/限制 | 说明 |
|------|------|-----------|------|
| `title` | ✅ | ≤500 字符 | 文章标题；纯文本即可，HTML 标签会被剥离 |
| `url` | ✅ | ≤1000 字符 | 必须以 http(s) 开头；会自动去除 utm 等跟踪参数后去重 |
| `source` | 建议 | ≤120 字符 | 来源名，如 `"Hacker News"`、`"你的任务名"`；会显示在列表页并可按来源筛选 |
| `published_at` | 可选 | ISO 8601，如 `2026-09-01T10:00:00` | 原文发布时间；没有就传空 |
| `summary` | 可选 | ≤2000 字符 | 原文摘要 / 正文开头几段；AI 摘要会参考它 |
| `content` | 可选 | ≤50000 字符 | 正文全文（纯文本）；详情页会展示前 3000 字"正文节选"，AI 摘要参考前 1200 字 |
| `tags` | 可选 | 字符串数组，≤5 个 | 原始标签（英文可保留），AI 之后会再补充中文标签 |

## 3. 去重规则

满足任一条件即判为重复（不写入，计入 `duplicates`）：
1. `url` 规范化（去 utm/ref 等、去尾部 `/`、域名小写）后已存在；
2. 完全相同的 `title` 已存在。

重复推送无副作用，放心重试。

## 4. 限频

每个 Token 60 秒内最多 120 次请求。批量推送（一请求多篇）可大幅降低请求数，**推荐每篇任务批量一次**。

## 5. 响应

成功（HTTP 200）：

```json
{ "inserted": 12, "duplicates": 3, "received": 15 }
```

| 错误码 | 含义 | 处理建议 |
|--------|------|----------|
| 401 | Token 无效或触发限频 | 检查 `Bearer ` 前缀与 Token；稍后重试 |
| 422 | 没有任何有效条目 | 检查 title/url 是否缺失、url 是否以 http 开头 |
| 429（ask 等其他接口） | 频率超限 | 降低频率 |

## 6. 调用示例

### curl

```bash
curl -X POST https://你的域名/api/v1/news \
  -H "Authorization: Bearer 你的Token" \
  -H "Content-Type: application/json" \
  -d '[
    {"title":"Example A","url":"https://example.com/a","source":"WorkBuddy采集","title_zh":"示例文章 A","ai_summary":"中文摘要……","category":"大模型","score":4},
    {"title":"Example B","url":"https://example.com/b","source":"WorkBuddy采集","content":"正文……","title_zh":"示例文章 B","ai_summary":"中文摘要……","category":"开源","score":3}
  ]'
```

### Python（requests）

```python
import requests

BASE = "https://你的域名"
TOKEN = "你的Token"

items = [
    {"title": t, "url": u, "source": "WorkBuddy采集",
     "published_at": ts, "summary": s, "content": body,
     "title_zh": t_zh, "ai_summary": zh_sum,
     "category": cat, "score": sc, "tags": tags, "entities": ents}
    for t, u, ts, s, body, t_zh, zh_sum, cat, sc, tags, ents in collected
]

resp = requests.post(f"{BASE}/api/v1/news",
                     json=items,
                     headers={"Authorization": f"Bearer {TOKEN}"},
                     timeout=30)
print(resp.status_code, resp.json())
```

无 requests 环境时可用 curl 子进程或任何 HTTP 客户端，接口无特殊要求。

## 7. 采集与整理流程（给代理的执行指引）

1. **每轮任务**：抓取新文章 → 用你自己的模型完成整理（翻译 `title_zh`、写 `ai_summary`、定 `category`、打 `score`）→ 批量推送。**整理在你的侧完成后推送，站内不再消耗 LLM 额度。**
2. **频率**：每 2–4 小时执行一轮即可，避免与站内 RSS 采集时段（07:00/12:00/20:00）完全重叠；建议错开到 09:30 / 14:30 / 22:00 等。
3. **增量**：每轮只抓上次运行之后的新内容；重复内容照常推送即可（自动去重），但不要整库重推。
4. **单轮上限**：一轮推送建议 ≤200 条，分批（每批 50 条）推送。
5. **失败重试**：非 200 响应等待 60 秒后重试一次；连续失败请停止本轮，避免堆积。
6. **内容质量**：只推 AI 相关内容；`title` 必须是文章真实标题，不要传列表页标题；`content` 优先传正文纯文本（去除导航/广告/脚本）。
7. **整理质量**：`ai_summary` 控制在 120 字内、写清"谁做了什么+关键数字/结论"；`category` 必须从枚举中选；`score` 请严格区分（普通动态 2–3，重大发布 4–5），它决定文章能否进"本周热门"和日报。
8. **时区**：`published_at` 用原文标注的本地时间即可，格式 `YYYY-MM-DDTHH:MM:SS`。

## 8. 推送后会发生什么（无需你处理）

1. 文章**立即出现在网站列表**（`/`），中文标题/摘要/分类/评分直接生效；
2. 若带了 AI 整理字段（`ai_summary` + `title_zh` 或 `category`）→ 标记为已处理，站内零 LLM 消耗；
3. 若缺 AI 字段 → 进入站内待处理队列，由站长的 Key 异步补齐（尽量都带上，避免占用站长额度）；
4. 每晚按 `digest_time`（默认 21:00）把当日高分文章（score 降序）整合成日报；若你已推送当日日报，站内会自动跳过、不会覆盖。

## 9. 日报推送接口（可选，推荐）

你也可以用自己的模型生成日报后推送，站内完全零 LLM 消耗：

`POST {BASE_URL}/api/v1/digest`，鉴权方式与文章推送相同。

```json
{
  "date": "2026-09-04",
  "top_events": [
    {"title": "事件标题", "comment": "一句话中文点评", "url": "https://example.com/post"}
  ],
  "highlights": {
    "大模型": ["要点标题 1", "要点标题 2"],
    "开源": ["要点标题"]
  },
  "trend": "不超过 300 字的今日趋势中文总结"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `date` | 可选 | `YYYY-MM-DD`，缺省为当天 |
| `top_events` | 建议 | 最重要的 3–5 条事件，每条含 title/comment/url |
| `highlights` | 建议 | 按分类的要点速览，最多 6 个分类、每类最多 5 条 |
| `trend` | 建议 | ≤300 字趋势总结 |

三者至少提供一项，否则返回 422。重复推送同一天会**覆盖**该日日报（可以随时修正）。站内生成逻辑遇到已有日报会自动跳过，因此**谁先推谁生效，后推覆盖**。

**建议节奏**：每晚 20:30 左右推送当日日报（在站内 digest_time 21:00 之前），保证日报稳定由你的模型生成。

## 10. 读取站内文章与更新

### 读取（无需 Token，公开接口）

```
GET {BASE_URL}/api/articles?status=pending&page=1&page_size=50
```

常用参数：

| 参数 | 说明 |
|------|------|
| `status` | `pending` / `done` / `failed`，筛选 AI 处理状态；**`pending` 即待你整理的文章** |
| `page` / `page_size` | 分页，page_size 最大 100 |
| `date` | 按日期筛选，如 `2026-09-04` |
| `source` / `category` / `tag` / `q` | 按来源/分类/标签/关键词筛选 |

返回项含 `ai_ready`（true=已处理）、`title`、`summary_raw`、`content`、`url` 等全部字段。文章详情也可用 `GET /api/articles/{id}`。

### 更新（需 Token）

`POST {BASE_URL}/api/v1/news/update`，按 `url` 定位文章，**只更新提供的字段**：

```json
{
  "url": "https://example.com/post",
  "title_zh": "中文标题",
  "ai_summary": "120 字内中文摘要",
  "category": "大模型",
  "score": 4,
  "tags": ["标签1", "标签2"],
  "entities": ["OpenAI"],
  "summary": "修正后的原文摘要",
  "content": "修正后的正文"
}
```

- `url` 必填（与推送时的 url 规范化规则一致）；返回 404 表示站内没有该文章
- 除 url 外至少提供一个字段，否则 422
- 若本次更新了 AI 字段且满足「有中文摘要 +（中文标题或分类）」，文章自动标记为已处理
- 支持数组批量：`[{...}, {...}]`，返回 `{"results": [...]}`

### 推荐工作流（代管站内 RSS 文章的整理）

1. `GET /api/articles?status=pending&page_size=100` 拉取待处理文章；
2. 用你的模型逐篇生成 `title_zh` / `ai_summary` / `category` / `score`；
3. `POST /api/v1/news/update` 批量写回（每批 ≤50 条）；
4. 重复直到 pending 清空。站内 AI 队列自此不再消耗站长 Key。
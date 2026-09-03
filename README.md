# AI 资讯站（news-ai）

每日自动采集 AI 资讯 + 大模型整合（摘要/评分/日报/问答）+ 本地任务推送接口。

架构：**前后端不分离** —— FastAPI + Jinja2 模板 + SQLite，单进程单目录，无 Node 工具链。样式为 Linear 深色设计系统（纯 CSS）。

环境要求：**Python 3.11**（服务器用 /study/env/python3.11/bin/python3；本地安装 `brew install python@3.11`）。

## 本地运行

```bash
cd backend
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # 按需修改，至少配置 LLM_API_KEY
./venv/bin/python run.py
```

访问：

- 前台首页 http://localhost:8001/
- AI 日报 http://localhost:8001/digest
- 资讯问答 http://localhost:8001/ask
- 管理后台 http://localhost:8001/admin （默认 admin / admin123，首次登录后立即改密）
- 接口文档 http://localhost:8001/api/docs

## 部署到服务器（CentOS 7）

整个项目只有一个 backend 目录需要上传。

1. 本地打包上传：

```bash
tar czf news-ai.tar.gz backend
scp news-ai.tar.gz root@服务器IP:/study/app/
```

2. 服务器上解压、建 venv、启动（必须独立 venv）：

```bash
mkdir -p /study/app && cd /study/app
tar xzf news-ai.tar.gz
cd news-ai/backend
/study/env/python3.11/bin/python3 -m venv venv
./venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
cp .env.example .env && vi .env
nohup ./venv/bin/python run.py > ../news-ai.log 2>&1 &
```

3. nginx 反代（新增 /usr/local/nginx/conf/conf.d/news-ai.conf）：

```nginx
server {
    listen 8092;
    server_name _;
    location /static/ {
        proxy_pass http://127.0.0.1:8001/static/;
    }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

然后 `/usr/local/nginx/sbin/nginx -s reload`。访问 http://服务器IP:8092 即可。

## 关键接口

| 接口 | 说明 |
|---|---|
| POST /api/v1/news | 本地推送文章（Bearer Token，支持单条/数组） |
| GET  /api/articles | 文章列表 JSON（sort/category/source/q/分页） |
| GET  /api/digests/latest | 最新日报 |
| POST /api/ask | 资讯问答 |
| POST /api/admin/login | 后台登录 |
| GET  /api/docs | 接口文档 |

## 目录结构

```
backend/
├── app/
│   ├── main.py           # 页面路由（Jinja2 SSR）+ 应用入口
│   ├── config.py         # .env 配置
│   ├── models.py         # SQLite 模型
│   ├── http_client.py    # 所有 HTTPS 走 subprocess+curl（适配无 _ssl 的服务器 Python）
│   ├── database.py       # 建表与初始数据
│   ├── templates/        # 页面模板（前台 + 后台）
│   ├── static/css/       # 全站样式（Linear 深色设计系统）
│   ├── api/              # public / ingest / admin JSON 接口
│   └── services/         # crawler 采集 / ai 管道 / scheduler 定时
├── run.py
├── requirements.txt
└── .env.example
```

## 注意

- 服务器 Python 缺 _ssl 模块：本项目所有出站 HTTPS 已统一走 `subprocess + curl`，无需额外处理
- 定时任务随进程运行（每日 07:00/12:00/20:00 采集，21:00 生成日报，均可在后台设置修改）
- 所有密钥只放 .env，勿提交到代码库

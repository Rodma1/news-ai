import sys
sys.modules["sqlite3"] = __import__("pysqlite3")

import uvicorn

from app.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        root_path="/news",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )

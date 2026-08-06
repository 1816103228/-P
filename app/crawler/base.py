"""爬虫适配器基类。

每个数据源继承 SourceAdapter，只需实现 fetch()；
fetch() 返回的每个 dict 是 app.db.upsert_question 的 kwargs：
  source, source_id, title, content, answer, tags, difficulty, url

make_session() 提供带重试与连接复用的 requests.Session，各适配器共享。
"""

from abc import ABC, abstractmethod

import requests


def make_session() -> requests.Session:
    """创建带 urllib3 重试与连接池的 Session（keep-alive 复用 TCP）。"""
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SourceAdapter(ABC):
    """数据源适配器基类。"""

    #: 来源标识（写入 questions.source），子类必须覆盖
    name: str = "base"

    @abstractmethod
    def fetch(self, limit: int | None = None) -> list[dict]:
        """抓取题目，返回入库字典列表。limit 用于调试时限制条数。"""

    def fetch_and_store(self, limit: int | None = None) -> dict:
        """抓取并入库，返回统计 {'source', 'new', 'skipped', 'error'?}。"""
        from app import db

        rows = self.fetch(limit=limit)
        if not rows:
            return {"source": self.name, "new": 0, "skipped": 0, "error": "no data"}
        for r in rows:
            r.setdefault("source", self.name)
        stats = db.upsert_many(rows)
        return {"source": self.name, **stats}

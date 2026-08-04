"""面试鸭适配器：抓取分类题库列表页（服务端渲染 HTML，无需登录）。

页面为 Ant Design Table，每行结构：
  tr.ant-table-row
    td: <a href="/question/{qid}">N. 题目</a>
    td: 难度（简单/中等/困难）
    td: span.ant-tag 标签列表（可能含 VIP 标记，跳过）

优化：共享 Session（连接复用 + 自动重试）、分类级并行抓取、
每页失败只告警不中断整类、limit 统一为"最多返回条数"。
"""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from app import config
from app.crawler.base import SourceAdapter, make_session

logger = logging.getLogger("interview_coach.crawler.mianshiya")

BASE = "https://www.mianshiya.com/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
}

#: 与 Python/后端面试相关的分类（对应页面 ?category= 参数）
CATEGORIES = [
    "python",
    "backend",
    "database",
    "computerNetwork",
    "os",
    "algorithm",
    "project",
]


class MianShiYaAdapter(SourceAdapter):
    """面试鸭题库（mianshiya.com）。"""

    name = "mianshiya"

    def __init__(self) -> None:
        self._session = make_session()

    def fetch(self, limit: int | None = None) -> list[dict]:
        """并行抓取全部分类，limit 为最多返回的题目条数（调试用）。"""
        pages = config.CRAWL_PAGES_PER_CATEGORY
        out: list[dict] = []
        with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as ex:
            futures = [ex.submit(self._fetch_category, cat, pages) for cat in CATEGORIES]
            for fut in as_completed(futures):
                try:
                    out.extend(fut.result())
                except Exception as e:  # 单分类失败不影响其他分类
                    logger.warning("mianshiya 分类抓取失败: %s", e)
        if limit:
            out = out[:limit]
        return out

    def _fetch_category(self, category: str, pages: int) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, pages + 1):
            try:
                url = f"{BASE}?category={category}&current={page}&pageSize=20"
                resp = self._session.get(url, headers=HEADERS, timeout=20)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "lxml")
                for tr in soup.select("tr.ant-table-row"):
                    cells = tr.select("td.ant-table-cell")
                    if len(cells) < 3:
                        continue
                    a = cells[0].find("a", href=True)
                    if not a:
                        continue
                    qid = a["href"].rsplit("/", 1)[-1]
                    title = re.sub(r"^\d+\.\s*", "", a.get_text(strip=True))
                    difficulty = cells[1].get_text(strip=True) or "中等"
                    tags = [t.get_text(strip=True) for t in cells[2].select("span.ant-tag")]
                    tags = [t for t in tags if t and t != "VIP"]
                    rows.append(
                        {
                            "source_id": qid,
                            "title": title,
                            "content": None,
                            "answer": None,
                            "tags": tags or [category],
                            "difficulty": difficulty,
                            "url": f"{BASE}question/{qid}",
                        }
                    )
            except Exception as e:  # 单页失败不中断整类
                logger.warning("mianshiya 分类 %s 第 %s 页抓取失败: %s", category, page, e)
            time.sleep(config.CRAWL_REQUEST_DELAY)
        return rows

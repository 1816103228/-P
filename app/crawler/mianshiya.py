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
    # 后端专题分类：与 Python/后端面试强相关，扩充题库
    "redis",
    "mysql",
    "middleware",
    "microservice",
    "mq",
    "docker",
    "kubernetes",
]

#: 详情页答案文本开头的界面噪音（推荐答案/视频讲解等按钮文案）
_ANSWER_NOISE = re.compile(
    r"^(推荐答案|视频讲解|测试一下|面试问答|开始面试|隐藏答案|回答重点|答案|复制|分享|展开|收起|关注)+\s*"
)
_ANSWER_MAX = 2000


def _clean_answer(text: str) -> str | None:
    """去掉答案前的界面按钮文案，截断到合理长度。"""
    t = _ANSWER_NOISE.sub("", text or "").strip()
    return t[:_ANSWER_MAX] if t else None


class MianShiYaAdapter(SourceAdapter):
    """面试鸭题库（mianshiya.com）。"""

    name = "mianshiya"

    def __init__(self) -> None:
        self._session = make_session()

    def fetch(self, limit: int | None = None) -> list[dict]:
        """并行抓取全部分类 + 热门题，再抓详情补全答案；limit 为最多返回条数（调试用）。"""
        pages = config.CRAWL_PAGES_PER_CATEGORY
        out: list[dict] = []
        with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as ex:
            futures = [ex.submit(self._fetch_category, cat, pages) for cat in CATEGORIES]
            futures.append(ex.submit(self._fetch_hot))
            for fut in as_completed(futures):
                try:
                    out.extend(fut.result())
                except Exception as e:  # 单分类/热门失败不影响其他
                    logger.warning("mianshiya 列表抓取失败: %s", e)
        out = self._dedupe_rows(out)
        out = self._enrich_details(out)
        if limit:
            out = out[:limit]
        return out

    def fetch_category(
        self,
        category: str,
        pages: int | None = None,
        limit: int | None = None,
        with_details: bool = False,
    ) -> list[dict]:
        """按需（懒加载）抓取单个分类的题：只抓列表页，秒级返回。

        默认不抓详情页（with_details=False），答案留待定时全量抓取补全；
        页面数默认取 config.CRAWL_PAGES_PER_CATEGORY，懒加载调用方可传小值加速。
        """
        pages = pages if pages is not None else config.CRAWL_PAGES_PER_CATEGORY
        rows = self._dedupe_rows(self._fetch_category(category, pages))
        if with_details:
            rows = self._enrich_details(rows)
        if limit:
            rows = rows[:limit]
        for r in rows:
            r.setdefault("source", self.name)
        return rows

    @staticmethod
    def _dedupe_rows(rows: list[dict]) -> list[dict]:
        """同一道题可能同时出现在多个分类/热门里，按 source_id 去重。"""
        seen: dict[str, dict] = {}
        for r in rows:
            seen.setdefault(r["source_id"], r)
        return list(seen.values())

    def _fetch_hot(self) -> list[dict]:
        """热门题列表：https://www.mianshiya.com/hot/question（额外的公开题目）。"""
        resp = self._session.get(f"{BASE}hot/question", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        rows: list[dict] = []
        for a in soup.select('a[href*="/question/"]'):
            qid = a["href"].rsplit("/", 1)[-1]
            if not qid.isdigit():
                continue
            title = re.sub(r"^\d+[.\s]*", "", a.get_text(strip=True))
            title = re.sub(r"[\d.]+k热度$", "", title).strip()
            if not title:
                continue
            rows.append(
                {
                    "source_id": qid,
                    "title": title,
                    "content": None,
                    "answer": None,
                    "tags": ["热门"],
                    "difficulty": "中等",
                    "url": f"{BASE}question/{qid}",
                }
            )
        return rows

    def _enrich_details(self, rows: list[dict]) -> list[dict]:
        """抓取每道题详情页，补全答案/难度/标签（详情页免登录可访问）。"""
        qid_map = {r["source_id"]: r for r in rows}
        with ThreadPoolExecutor(max_workers=config.CRAWL_WORKERS) as ex:
            futures = {ex.submit(self._fetch_detail, qid): qid for qid in qid_map}
            for fut in as_completed(futures):
                qid = futures[fut]
                try:
                    detail = fut.result()
                except Exception as e:
                    logger.warning("mianshiya 详情抓取失败 %s: %s", qid, e)
                    continue
                if detail:
                    row = qid_map[qid]
                    for k, v in detail.items():
                        if v:
                            row[k] = v
        return rows

    def _fetch_detail(self, qid: str) -> dict | None:
        """单个题目详情页：提取真实标题、难度、标签与推荐答案。"""
        resp = self._session.get(f"{BASE}question/{qid}", headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        h1 = soup.select_one("h1")
        if not h1:
            return None
        title = re.sub(r"^\d+[.\s]*", "", h1.get_text(strip=True))
        tags = [t.get_text(strip=True) for t in soup.select(".ant-tag")]
        difficulty = "中等"
        real_tags: list[str] = []
        for t in tags:
            if t in ("简单", "中等", "困难"):
                difficulty = t
            elif t != "VIP":
                real_tags.append(t)
        answer = None
        ans_el = soup.select_one('[class*="answer"]')
        if ans_el:
            answer = _clean_answer(ans_el.get_text(" ", strip=True))
        return {
            "title": title,
            "difficulty": difficulty,
            "tags": real_tags or None,
            "answer": answer,
        }

    def after_store(self, rows: list[dict]) -> None:
        """入库后把抓到的答案/难度回写已存在的题目（详情补全）。"""
        from app import db

        for r in rows:
            if not (r.get("answer") or r.get("difficulty")):
                continue
            db.update_question_details(
                self.name,
                r["source_id"],
                answer=r.get("answer"),
                difficulty=r.get("difficulty"),
            )

    def _fetch_category(self, category: str, pages: int) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, pages + 1):
            try:
                url = f"{BASE}?category={category}&current={page}&pageSize=20"
                resp = self._session.get(url, headers=HEADERS, timeout=30)
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

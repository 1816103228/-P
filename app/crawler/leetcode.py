"""LeetCode 适配器：使用官方公开 API（无需登录），抓取全部免费题目。

接口：https://leetcode.cn/api/problems/all/ 返回 JSON，
字段含 question_id / question__title / question__title_slug /
frontend_question_id / difficulty.level(1简单 2中等 3困难) / paid_only。

优化：响应本地缓存（TTL 可配），接口失败（如 403）时降级用过期缓存，
而不是让整个爬取链路报错。
"""

import json
import logging
import time

import requests

from app import config
from app.crawler.base import SourceAdapter, make_session

logger = logging.getLogger("interview_coach.crawler.leetcode")

API_URL = "https://leetcode.cn/api/problems/all/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DIFF_MAP = {1: "简单", 2: "中等", 3: "困难"}

CACHE_FILE = config.DATA_DIR / "leetcode_cache.json"
CACHE_TTL_SECONDS = config.LEETCODE_CACHE_HOURS * 3600


class LeetCodeAdapter(SourceAdapter):
    """力扣（中国站）算法题。"""

    name = "leetcode"

    def __init__(self) -> None:
        self._session = make_session()

    def fetch(self, limit: int | None = None) -> list[dict]:
        data = self._load_cache()
        if data is None:
            try:
                resp = self._session.get(API_URL, headers=HEADERS, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                self._save_cache(data)
            except (requests.RequestException, ValueError) as e:
                data = self._load_cache(ignore_ttl=True)
                if data is None:
                    raise RuntimeError(f"LeetCode 接口请求失败且无本地缓存: {e}") from e
                logger.warning("LeetCode 接口请求失败，使用过期本地缓存: %s", e)

        out: list[dict] = []
        for p in data.get("stat_status_pairs", []):
            if p.get("paid_only"):
                continue  # 跳过会员题
            st = p["stat"]
            fid = st.get("frontend_question_id", "")
            out.append(
                {
                    "source_id": str(fid),
                    "title": f"[LeetCode {fid}] {st.get('question__title', '')}",
                    "content": None,
                    "answer": None,
                    "tags": ["算法", "数据结构"],
                    "difficulty": DIFF_MAP.get(p["difficulty"]["level"], "中等"),
                    "url": f"https://leetcode.cn/problems/{st.get('question__title_slug', '')}/",
                }
            )
        if limit:
            out = out[:limit]
        return out

    @classmethod
    def _save_cache(cls, data: dict) -> None:
        try:
            config.ensure_data_dir()
            payload = {"fetched_at": time.time(), "data": data}
            CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.warning("LeetCode 缓存写入失败: %s", e)

    @classmethod
    def _load_cache(cls, ignore_ttl: bool = False):
        """读取本地缓存；ignore_ttl=True 时允许使用过期缓存（降级）。"""
        try:
            payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            age = time.time() - payload["fetched_at"]
            if ignore_ttl or 0 <= age <= CACHE_TTL_SECONDS:
                return payload["data"]
        except (OSError, ValueError, KeyError):
            pass
        return None

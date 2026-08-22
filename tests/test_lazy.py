"""懒加载补抓与后台追答案测试（mock 适配器与 db，不触网）。"""

import unittest
from unittest import mock

from app.crawler import lazy


class BackfillSourceIdsTests(unittest.TestCase):
    def test_backfill_returns_source_ids(self):
        """补抓成功后返回按源分组的 source_id 列表（供后台追答案）。"""
        fake_rows = [
            {"source_id": "111", "title": "题1", "tags": ["Python"]},
            {"source_id": "222", "title": "题2", "tags": ["Python"]},
        ]
        with (
            mock.patch("app.crawler.lazy._fetch_category", return_value=fake_rows),
            mock.patch("app.crawler.lazy.db.is_category_fetched", return_value=False),
            mock.patch("app.crawler.lazy.db.mark_category_fetched"),
            mock.patch("app.crawler.lazy.config.LAZY_CRAWL_TIMEOUT", 30),
        ):
            # 只传 keywords，避免岗位名同时命中多个分类映射
            res = lazy.backfill_for_job("", "", ["python"])
        self.assertEqual(res["new"], 2)
        self.assertEqual(res["source_ids"], {"mianshiya": ["111", "222"]})

    def test_backfill_zero_hits_no_source_ids(self):
        with (
            mock.patch("app.crawler.lazy._fetch_category", return_value=[]),
            mock.patch("app.crawler.lazy.db.is_category_fetched", return_value=False),
            mock.patch("app.crawler.lazy.db.mark_category_fetched"),
            mock.patch("app.crawler.lazy.config.LAZY_CRAWL_TIMEOUT", 30),
        ):
            res = lazy.backfill_for_job("Python 后端", "", ["python"])
        self.assertEqual(res["new"], 0)
        self.assertEqual(res["source_ids"], {})


class EnrichAnswersTests(unittest.TestCase):
    def test_enrich_answers_delegates(self):
        """同步追答案：委托适配器 fetch_details_for 抓详情补答案。"""
        adapter = mock.Mock()
        adapter.fetch_details_for.return_value = {"total": 2, "updated": 2}
        with mock.patch("app.crawler.lazy._get_adapter", return_value=adapter):
            stats = lazy.enrich_answers("mianshiya", ["111", "222"])
        self.assertEqual(stats, {"total": 2, "updated": 2})
        adapter.fetch_details_for.assert_called_once_with(["111", "222"])

    def test_enrich_answers_unsupported_source(self):
        """不支持追答案的源：不抛异常，返回 error 标记。"""
        with mock.patch("app.crawler.lazy._get_adapter", return_value=None):
            stats = lazy.enrich_answers("unknown", ["1"])
        self.assertEqual(stats["error"], "unsupported")

    def test_enrich_answers_empty(self):
        self.assertEqual(lazy.enrich_answers("mianshiya", []), {"total": 0, "updated": 0})

    def test_enrich_answers_async_spawns_thread(self):
        """异步追答案：起后台 daemon 线程，不阻塞主流程。"""
        with mock.patch("app.crawler.lazy.threading.Thread") as mt:
            ok = lazy.enrich_answers_async("mianshiya", ["111", "222"])
        self.assertTrue(ok)
        mt.assert_called_once()
        self.assertTrue(mt.call_args.kwargs["daemon"])
        mt.return_value.start.assert_called_once()

    def test_enrich_answers_async_empty(self):
        with mock.patch("app.crawler.lazy.threading.Thread") as mt:
            self.assertFalse(lazy.enrich_answers_async("mianshiya", []))
        mt.assert_not_called()


if __name__ == "__main__":
    unittest.main()

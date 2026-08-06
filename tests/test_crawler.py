"""爬虫适配器测试（mock 网络响应，不触网）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests
from app.crawler import leetcode, mianshiya, run

MIANSHIYA_ROW = """
<table><tbody>
<tr class="ant-table-row">
  <td class="ant-table-cell"><a href="/question/123">1. 谈谈 GIL</a></td>
  <td class="ant-table-cell">中等</td>
  <td class="ant-table-cell"><span class="ant-tag">Python</span><span class="ant-tag">VIP</span></td>
</tr>
</tbody></table>
"""

LEETCODE_PAYLOAD = {
    "stat_status_pairs": [
        {
            "stat": {
                "frontend_question_id": "1",
                "question__title": "Two Sum",
                "question__title_slug": "two-sum",
            },
            "difficulty": {"level": 1},
            "paid_only": False,
        },
        {
            "stat": {
                "frontend_question_id": "2",
                "question__title": "Add Two Numbers",
                "question__title_slug": "add-two-numbers",
            },
            "difficulty": {"level": 2},
            "paid_only": False,
        },
        {
            "stat": {
                "frontend_question_id": "3",
                "question__title": "Paid Question",
                "question__title_slug": "paid",
            },
            "difficulty": {"level": 3},
            "paid_only": True,
        },
    ]
}


def _fake_get(text: str = "", json_data=None):
    resp = mock.Mock()
    resp.text = text
    resp.json.return_value = json_data
    return resp


class MianShiYaTests(unittest.TestCase):
    @mock.patch("requests.Session.get", return_value=_fake_get(MIANSHIYA_ROW))
    def test_parse_row(self, mock_get):
        ad = mianshiya.MianShiYaAdapter()
        rows = ad._fetch_category("python", 1)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["source_id"], "123")
        self.assertEqual(r["title"], "谈谈 GIL")
        self.assertEqual(r["difficulty"], "中等")
        self.assertEqual(r["tags"], ["Python"])  # VIP 标签被过滤

    @mock.patch("requests.Session.get", return_value=_fake_get(MIANSHIYA_ROW))
    def test_fetch_limit_caps_rows(self, mock_get):
        ad = mianshiya.MianShiYaAdapter()
        with (
            mock.patch.object(mianshiya.config, "CRAWL_WORKERS", 1),
            mock.patch.object(mianshiya.config, "CRAWL_REQUEST_DELAY", 0.0),
            mock.patch.object(mianshiya.config, "CRAWL_PAGES_PER_CATEGORY", 1),
        ):
            rows = ad.fetch(limit=2)
        self.assertEqual(len(rows), 2)


class LeetCodeTests(unittest.TestCase):
    @mock.patch("requests.Session.get", return_value=_fake_get(json_data=LEETCODE_PAYLOAD))
    def test_parse_and_skip_paid(self, mock_get):
        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch.object(leetcode, "CACHE_FILE", Path(td) / "cache.json"),
        ):
            ad = leetcode.LeetCodeAdapter()
            rows = ad.fetch()
        self.assertEqual(len(rows), 2)  # 跳过 VIP 题
        self.assertIn("[LeetCode 1]", rows[0]["title"])
        self.assertEqual(rows[0]["difficulty"], "简单")
        self.assertEqual(rows[1]["difficulty"], "中等")

    @mock.patch("requests.Session.get", return_value=_fake_get(json_data=LEETCODE_PAYLOAD))
    def test_limit(self, mock_get):
        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch.object(leetcode, "CACHE_FILE", Path(td) / "cache.json"),
        ):
            ad = leetcode.LeetCodeAdapter()
            rows = ad.fetch(limit=1)
        self.assertEqual(len(rows), 1)

    def test_cache_fallback_on_network_error(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            with mock.patch.object(leetcode, "CACHE_FILE", cache):
                leetcode.LeetCodeAdapter._save_cache(LEETCODE_PAYLOAD)
                with mock.patch(
                    "requests.Session.get", side_effect=requests.RequestException("网络错误")
                ):
                    ad = leetcode.LeetCodeAdapter()
                    rows = ad.fetch()
        self.assertEqual(len(rows), 2)

    def test_no_cache_raises_on_network_error(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "missing.json"
            with (
                mock.patch.object(leetcode, "CACHE_FILE", cache),
                mock.patch(
                    "requests.Session.get", side_effect=requests.RequestException("网络错误")
                ),
            ):
                ad = leetcode.LeetCodeAdapter()
                with self.assertRaises(RuntimeError):
                    ad.fetch()


class RunAdapterTests(unittest.TestCase):
    def test_crawl_all_passes_limit(self):
        m1 = mock.MagicMock(return_value={"source": "mianshiya", "new": 1})
        m2 = mock.MagicMock(return_value={"source": "leetcode", "new": 1})
        m3 = mock.MagicMock(return_value={"source": "nowcoder", "new": 0})
        with (
            mock.patch.object(run.mianshiya.MianShiYaAdapter, "fetch_and_store", m1),
            mock.patch.object(run.leetcode.LeetCodeAdapter, "fetch_and_store", m2),
            mock.patch.object(run.nowcoder.NowCoderAdapter, "fetch_and_store", m3),
        ):
            stats = run.crawl_all(limit_per_source=3)
        self.assertEqual(len(stats), 3)
        m1.assert_called_once_with(limit=3)
        m2.assert_called_once_with(limit=3)
        m3.assert_called_once_with(limit=3)


if __name__ == "__main__":
    unittest.main()

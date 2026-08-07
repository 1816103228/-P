"""批量导入自定义题库测试：CSV 解析与入库统计（mock db，不触网）。"""
import unittest
from unittest import mock

from app import importer


class ImporterTests(unittest.TestCase):
    def test_parse_csv_with_header(self):
        text = "题目,答案,标签,难度,公司\n解释 GIL,锁机制,Python,简单,通用\nRedis 穿透,缓存,Redis,中等,字节"
        rows = importer.parse_questions_csv(text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["title"], "解释 GIL")
        self.assertEqual(rows[0]["tags"], ["Python"])
        self.assertEqual(rows[0]["difficulty"], "简单")
        self.assertEqual(rows[1]["company"], "字节")
        self.assertEqual(rows[0]["source"], "custom")

    def test_parse_csv_without_header_positional(self):
        text = "Q1,答案一\nQ2,答案二,Redis\n"
        rows = importer.parse_questions_csv(text)
        self.assertEqual(rows[0]["title"], "Q1")
        self.assertEqual(rows[0]["answer"], "答案一")
        self.assertEqual(rows[1]["tags"], ["Redis"])

    def test_parse_csv_skips_empty_title_and_bad_difficulty(self):
        text = "题目,难度\n有效题,中等\n,困难\n另一题,超级难"
        rows = importer.parse_questions_csv(text)
        self.assertEqual([r["title"] for r in rows], ["有效题", "另一题"])
        self.assertIsNone(rows[1]["difficulty"])

    def test_parse_empty(self):
        self.assertEqual(importer.parse_questions_csv(""), [])
        self.assertEqual(importer.parse_questions_csv("\n\n"), [])

    def test_import_calls_upsert_many(self):
        with mock.patch(
            "app.importer.db.upsert_many", return_value={"new": 2, "skipped": 1}
        ) as mock_upsert:
            stats = importer.import_questions_csv("题目\nA\nB\nC")
        mock_upsert.assert_called_once()
        self.assertEqual(stats, {"new": 2, "skipped": 1, "rows": 3})

    def test_import_empty_no_db_call(self):
        with mock.patch("app.importer.db.upsert_many") as mock_upsert:
            stats = importer.import_questions_csv("")
        mock_upsert.assert_not_called()
        self.assertEqual(stats, {"new": 0, "skipped": 0, "rows": 0})


if __name__ == "__main__":
    unittest.main()

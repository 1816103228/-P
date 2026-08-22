"""统一清洗层测试：clean_status 状态机（临时库 + mock LLM，离线）。"""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from app import config, db
from app.crawler import clean

#: 语义清洗 mock 返回的 LLM 回复（json_object 格式）
_SEMANTIC_REPLY = '{"items": [{"index": 0, "tags": ["数据库"]}]}'


class CleanStateMachineTests(unittest.TestCase):
    """清洗状态机：raw → rule_cleaned → semantic_cleaned / ready。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.DB_PATH
        config.DB_PATH = Path(self._tmp) / "test.db"
        db.init_db()

    def tearDown(self):
        config.DB_PATH = self._orig

    def _row(self, qid: int):
        return db.get_question_by_id(qid)

    def test_upsert_defaults_to_raw(self):
        """新入库默认 clean_status = raw。"""
        db.upsert_question(source="custom", title="默认状态题")
        row = db.search_questions(keyword="默认状态题", limit=1)[0]
        self.assertEqual(row["clean_status"], db.CLEAN_STATUS_RAW)

    def test_migration_adds_columns(self):
        """迁移后 questions 表含清洗状态三列与索引。"""
        with closing(db.get_conn()) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)").fetchall()}
            idxs = {r[1] for r in conn.execute("PRAGMA index_list(questions)").fetchall()}
        for c in ("clean_status", "clean_version", "cleaned_at"):
            self.assertIn(c, cols)
        self.assertIn("idx_questions_clean_status", idxs)

    def test_rule_clean_cleans_fields_and_ready(self):
        """规则清洗：清理标题/答案/标签/难度；标签达标直接到 ready。"""
        db.upsert_question(
            source="custom",
            title=" 1. 缓存穿透 ",
            answer="  参考答案  ",
            tags=["Redis", "Redis", "VIP"],
            difficulty="",
        )
        row = db.search_questions(keyword="缓存穿透", limit=1)[0]
        stats = clean.run_rule_clean()
        row2 = self._row(row["id"])
        self.assertEqual(row2["title"], "缓存穿透")  # 题号 + 空白被清理
        self.assertEqual(row2["answer"], "参考答案")  # 空白被清理
        self.assertEqual(row2["tags"], "Redis")  # 去重 + 去 VIP 噪音
        self.assertEqual(row2["difficulty"], "中等")  # 空难度回填
        self.assertEqual(row2["clean_status"], db.CLEAN_STATUS_READY)  # 标签达标 → ready
        self.assertEqual(row2["clean_version"], clean.CLEAN_VERSION)
        self.assertEqual(stats["ready"], 1)

    def test_rule_clean_marks_rule_cleaned_when_coarse(self):
        """标签粗糙（算法）→ 规则清洗后进入 rule_cleaned，等语义清洗。"""
        db.upsert_question(source="custom", title="两数之和", tags=["算法"])
        row = db.search_questions(keyword="两数之和", limit=1)[0]
        clean.run_rule_clean()
        self.assertEqual(self._row(row["id"])["clean_status"], db.CLEAN_STATUS_RULE)

    def test_semantic_clean_mock_llm(self):
        """语义清洗：mock LLM 打标，标签写回 + 状态到 semantic_cleaned。"""
        db.upsert_question(source="custom", title="什么是 TCP 三次握手", tags=["算法"])
        row = db.search_questions(keyword="TCP", limit=1)[0]
        clean.run_rule_clean()  # 粗标签 → rule_cleaned
        with mock.patch("app.crawler.classify.chat", return_value=_SEMANTIC_REPLY):
            stats = clean.run_semantic_clean()
        self.assertEqual(stats["labeled"], 1)
        row2 = self._row(row["id"])
        self.assertEqual(row2["tags"], "数据库")  # LLM 标签写回
        self.assertEqual(row2["clean_status"], db.CLEAN_STATUS_SEMANTIC)
        self.assertEqual(row2["clean_version"], clean.CLEAN_VERSION)

    def test_semantic_clean_skips_ready_rows(self):
        """标签已达标的题不进入语义清洗。"""
        db.upsert_question(source="custom", title="Redis 持久化", tags=["Redis"])
        clean.run_rule_clean()  # → ready
        with mock.patch("app.crawler.classify.chat", return_value=_SEMANTIC_REPLY) as m:
            stats = clean.run_semantic_clean()
        m.assert_not_called()
        self.assertEqual(stats["scanned"], 0)

    def test_version_expired_recleaned(self):
        """规则版本升级后，旧版本数据被精准重洗。"""
        db.upsert_question(source="custom", title="旧版本题", tags=["Redis"])
        row = db.search_questions(keyword="旧版本题", limit=1)[0]
        clean.run_rule_clean()  # → ready + 当前版本
        row2 = self._row(row["id"])
        self.assertEqual(row2["clean_status"], db.CLEAN_STATUS_READY)
        self.assertEqual(row2["clean_version"], clean.CLEAN_VERSION)
        # 模拟规则升级：新版本下该行应进入待重洗集合
        pending = db.list_pending_rule_clean("NEW.VERSION")
        self.assertTrue(any(r["id"] == row["id"] for r in pending))

    def test_clean_stats(self):
        """清洗统计：分状态计数 + 终态总数。"""
        db.upsert_question(source="custom", title="统计题A", tags=["Redis"])
        db.upsert_question(source="custom", title="统计题B", tags=["算法"])
        db.upsert_question(source="custom", title="统计题C", tags=["Redis"])
        clean.run_rule_clean()  # A、C → ready；B → rule_cleaned
        stats = clean.clean_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats[db.CLEAN_STATUS_READY], 2)
        self.assertEqual(stats[db.CLEAN_STATUS_RULE], 1)
        self.assertEqual(stats["done"], 2)

    def test_reset_clean_status(self):
        """重置状态：可把某状态批量退回（精准重洗辅助）。"""
        db.upsert_question(source="custom", title="重置题", tags=["Redis"])
        clean.run_rule_clean()
        n = db.reset_clean_status(db.CLEAN_STATUS_READY, db.CLEAN_STATUS_RAW)
        self.assertEqual(n, 1)
        row = db.search_questions(keyword="重置题", limit=1)[0]
        self.assertEqual(row["clean_status"], db.CLEAN_STATUS_RAW)


if __name__ == "__main__":
    unittest.main()

"""voice_store：按用户存储的定制面试状态（DB 承载，替代旧单文件）。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app.db as db
from app import config, voice_store


class VoiceStoreTests(unittest.TestCase):
    def setUp(self):
        # 隔离数据库，避免污染真实 data/questions.db
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test_store.db"
        self._patch = mock.patch.object(config, "DB_PATH", self._db_path)
        self._patch.start()
        db.init_db()
        # 两个测试用户，验证按用户隔离
        self.uid_a = db.create_user("user_a", "x")
        self.uid_b = db.create_user("user_b", "x")

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_save_load_roundtrip(self):
        voice_store.save_custom_interview(self.uid_a, "Python 后端", "JD 内容", ["Q1", "Q2"])
        data = voice_store.load_custom_interview(self.uid_a)
        self.assertEqual(data["job_title"], "Python 后端")
        self.assertEqual(data["jd"], "JD 内容")
        self.assertEqual(data["questions"], ["Q1", "Q2"])

    def test_save_filters_empty_questions(self):
        voice_store.save_custom_interview(self.uid_a, "岗位", "", ["", "  ", "Q1"])
        data = voice_store.load_custom_interview(self.uid_a)
        self.assertEqual(data["questions"], ["Q1"])

    def test_load_missing_returns_none(self):
        self.assertIsNone(voice_store.load_custom_interview(self.uid_a))

    def test_per_user_isolation(self):
        """用户 A 的定制面试不会泄漏给用户 B。"""
        voice_store.save_custom_interview(self.uid_a, "A", "", ["Qa"])
        self.assertIsNone(voice_store.load_custom_interview(self.uid_b))
        voice_store.save_custom_interview(self.uid_b, "B", "", ["Qb"])
        self.assertEqual(voice_store.load_custom_interview(self.uid_a)["questions"], ["Qa"])
        self.assertEqual(voice_store.load_custom_interview(self.uid_b)["questions"], ["Qb"])

    def test_clear_removes(self):
        voice_store.save_custom_interview(self.uid_a, "X", "", ["Q"])
        self.assertIsNotNone(voice_store.load_custom_interview(self.uid_a))
        voice_store.clear_custom_interview(self.uid_a)
        self.assertIsNone(voice_store.load_custom_interview(self.uid_a))

    def test_save_overwrites(self):
        voice_store.save_custom_interview(self.uid_a, "Y", "", ["Q2"])
        voice_store.save_custom_interview(self.uid_a, "Z", "", ["Q3"])
        data = voice_store.load_custom_interview(self.uid_a)
        self.assertEqual(data["job_title"], "Z")
        self.assertEqual(data["questions"], ["Q3"])


if __name__ == "__main__":
    unittest.main()

"""voice_store：文字版/语音版共享的定制面试状态（临时文件，不触网）。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import voice_store


class VoiceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp_file = Path(self.tmpdir.name) / "voice_custom_interview.json"
        self.patch = mock.patch.object(voice_store, "_CUSTOM_FILE", self.tmp_file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmpdir.cleanup()

    def test_save_load_roundtrip(self):
        voice_store.save_custom_interview("Python 后端", "JD 内容", ["Q1", "Q2"])
        data = voice_store.load_custom_interview()
        self.assertEqual(data["job_title"], "Python 后端")
        self.assertEqual(data["jd"], "JD 内容")
        self.assertEqual(data["questions"], ["Q1", "Q2"])

    def test_save_filters_empty_questions(self):
        voice_store.save_custom_interview("岗位", "", ["", "  ", "Q1"])
        data = voice_store.load_custom_interview()
        self.assertEqual(data["questions"], ["Q1"])

    def test_load_missing_returns_none(self):
        self.assertIsNone(voice_store.load_custom_interview())

    def test_load_corrupt_returns_none(self):
        self.tmp_file.write_text("{broken json", encoding="utf-8")
        self.assertIsNone(voice_store.load_custom_interview())

    def test_clear_removes_file(self):
        voice_store.save_custom_interview("X", "", ["Q"])
        self.assertIsNotNone(voice_store.load_custom_interview())
        voice_store.clear_custom_interview()
        self.assertIsNone(voice_store.load_custom_interview())


if __name__ == "__main__":
    unittest.main()

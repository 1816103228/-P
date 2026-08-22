"""多用户专项测试：认证散列、会话序列化与持久化、定制面试 SSE 生成流程。

使用临时数据库 + mock LLM/定制生成器，全程不触网。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app.db as db
import app.session_store as session_store
from app import auth, config
from app.agent.coach import InterviewSession


def _fresh_db(testcase):
    """建立临时数据库隔离，返回 (临时目录句柄, 清理函数)。"""
    tmpdir = tempfile.TemporaryDirectory()
    db_path = Path(tmpdir.name) / "test_multi.db"
    patch = mock.patch.object(config, "DB_PATH", db_path)
    patch.start()
    db.init_db()
    return tmpdir, patch


class AuthHashTests(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        h = auth.hash_password("my-secret-123")
        self.assertTrue(h.startswith("pbkdf2$"))
        self.assertTrue(auth.verify_password("my-secret-123", h))
        self.assertFalse(auth.verify_password("wrong", h))
        self.assertFalse(auth.verify_password("my-secret-123", "garbage"))

    def test_hash_is_salted(self):
        h1 = auth.hash_password("same-password")
        h2 = auth.hash_password("same-password")
        self.assertNotEqual(h1, h2, "随机盐应让相同密码产生不同散列")
        self.assertTrue(auth.verify_password("same-password", h1))
        self.assertTrue(auth.verify_password("same-password", h2))

    def test_token_resolution(self):
        tmpdir, patch = _fresh_db(self)
        try:
            uid = db.create_user("alice", auth.hash_password("secret123"))
            token = auth.issue_token(uid)
            user = auth.resolve_token_user(token)
            self.assertIsNotNone(user)
            self.assertEqual(user["id"], uid)
            self.assertIsNone(auth.resolve_token_user("invalid-token"))
            self.assertIsNone(auth.resolve_token_user(None))
            # 注销后失效
            db.revoke_token(token)
            self.assertIsNone(auth.resolve_token_user(token))
        finally:
            patch.stop()
            tmpdir.cleanup()


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test_sess.db"
        self._patch = mock.patch.object(config, "DB_PATH", self._db_path)
        self._patch.start()
        db.init_db()
        self.uid = db.create_user("bob", auth.hash_password("secret123"))

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_to_dict_from_dict_roundtrip(self):
        s = InterviewSession(
            "mock",
            questions=["Q1", "Q2"],
            job_title="Python 后端",
            jd="JD",
            persona="一面 · 同级工程师",
        )
        s.turn = "answering"
        s.asked_ids = {1, 2, 3}
        s.messages.append({"role": "user", "content": "自我介绍"})
        s.messages.append({"role": "assistant", "content": "好的"})
        s.answers.append({"stage": "定制题 1", "title": "Q1", "answer": "答"})
        s.display_history = [["assistant", "欢迎"], ["user", "你好"], ["assistant", "回复"]]
        s2 = InterviewSession.from_dict(s.to_dict())
        self.assertEqual(s2.mode, "mock")
        self.assertEqual(s2.turn, "answering")
        self.assertEqual(s2.asked_ids, {1, 2, 3})
        self.assertEqual(s2.custom_questions, ["Q1", "Q2"])
        self.assertEqual(s2.job_title, "Python 后端")
        self.assertEqual(s2.history_for_display(), s.history_for_display())
        self.assertEqual(s2.messages, s.messages)
        self.assertEqual(s2.answers, s.answers)

    def test_start_save_load_archive_flow(self):
        s = InterviewSession("coach")
        s.display_history = [["assistant", "欢迎"]]
        sid = session_store.start_session(self.uid, s)
        self.assertIsNotNone(sid)
        self.assertEqual(s.session_id, sid)
        # 落库后能恢复
        loaded = session_store.load_active_session(self.uid)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.mode, "coach")
        self.assertEqual(loaded.history_for_display(), s.history_for_display())
        # 归档后无活跃会话
        session_store.archive_current(self.uid)
        self.assertIsNone(session_store.load_active_session(self.uid))
        # 历史记录仍可查
        hist = session_store.list_history(self.uid)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["status"], "done")

    def test_save_session_updates_state(self):
        s = InterviewSession("coach")
        s.display_history = [["assistant", "欢迎"]]
        session_store.start_session(self.uid, s)
        s.display_history.append(["user", "问题"])
        s.display_history.append(["assistant", "回答"])
        session_store.save_session(self.uid, s)
        loaded = session_store.load_active_session(self.uid)
        self.assertEqual(
            loaded.display_history, [["assistant", "欢迎"], ["user", "问题"], ["assistant", "回答"]]
        )

    def test_user_sessions_isolated(self):
        """不同用户的活跃会话互不干扰。"""
        uid_b = db.create_user("bob2", "x")
        session_store.start_session(self.uid, InterviewSession("coach"))
        self.assertIsNone(session_store.load_active_session(uid_b))
        session_store.start_session(uid_b, InterviewSession("mock"))
        self.assertEqual(session_store.load_active_session(self.uid).mode, "coach")
        self.assertEqual(session_store.load_active_session(uid_b).mode, "mock")


class CustomGenerateSseTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test_custom.db"
        self._patch = mock.patch.object(config, "DB_PATH", self._db_path)
        self._patch.start()
        db.init_db()
        self.uid = db.create_user("carol", auth.hash_password("secret123"))
        self.token = auth.issue_token(self.uid)
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_custom_generate_sse_starts_session(self):
        """定制面试生成（SSE）→ 自动进入该定制模拟面试 + 语音定制按用户保存。"""
        from app.voice_server import app
        from fastapi.testclient import TestClient

        meta = {
            "sources": ["题库"] * 2,
            "bank_hits": [],
            "lazy": {"attempted": 0, "new": 0, "detail": ""},
            "lazy_fetched": False,
            "answer_backfill": False,
        }
        with (
            TestClient(app) as client,
            mock.patch(
                "app.routers.custom.generate_interview_questions_with_meta",
                return_value=(["Q1：介绍 GIL", "Q2：设计限流"], meta),
            ),
        ):
            r = client.post(
                "/api/custom/generate",
                json={"job_title": "Python 后端", "jd": "熟悉 Redis"},
                headers=self.headers,
            )
            self.assertEqual(r.status_code, 200)
            body = r.text
            self.assertIn('"type": "done"', body)
            self.assertIn("定制题", body)
            self.assertIn("2 道定制题", body)
            # 会话已进入定制模拟面试
            s = client.get("/api/session", headers=self.headers).json()
            self.assertTrue(s["active"])
            self.assertEqual(s["mode"], "mock")
            self.assertEqual(s["custom_questions"], ["Q1：介绍 GIL", "Q2：设计限流"])
            # 语音定制面试按用户保存
            st = client.get("/api/custom/status", headers=self.headers).json()
            self.assertTrue(st["ready"])
            self.assertEqual(st["job_title"], "Python 后端")

    def test_custom_generate_empty_input_rejected(self):
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            r = client.post(
                "/api/custom/generate", json={"job_title": "", "jd": ""}, headers=self.headers
            )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()

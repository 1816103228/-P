"""题库浏览功能验证（标准库 unittest）。

运行：
    python -m unittest app.tests.test_question_bank -v

注意：设置 DISABLE_SCHEDULER=1 避免 AppTest 触发真实后台爬虫；
数据类测试在空库时自动跳过（先跑爬虫抓取）。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["DISABLE_SCHEDULER"] = "1"

from app import config, db
from app.agent.coach import InterviewSession


class QuestionBankChecks(unittest.TestCase):
    """数据层与教练层的新增能力。"""

    def setUp(self):
        # 全新环境（无 data/questions.db）下先建库，避免 count_questions 抛错
        try:
            db.init_db()
            n = db.count_questions()
        except Exception:
            n = 0
        if n == 0:
            self.skipTest("题库为空，请先运行 python -m app.crawler.run")

    def test_search_by_keyword(self):
        rows = db.search_questions(keyword="Redis", limit=5)
        if not rows:
            self.skipTest("题库中没有 Redis 相关题（数据依赖，跳过）")
        for r in rows:
            self.assertIn("Redis", r["title"])

    def test_fts_trigram_query_rules(self):
        """trigram 查询串规则：全部词 ≥3 字符才走 trigram；短词/混合词回退。"""
        self.assertIsNotNone(db._fts_trigram_query("缓存穿透"))
        self.assertIsNotNone(db._fts_trigram_query("Redis缓存一致性"))
        self.assertIsNone(db._fts_trigram_query("缓存"))
        self.assertIsNone(db._fts_trigram_query("Redis 缓存"))
        self.assertIsNone(db._fts_trigram_query(""))

    def test_fts_search_trigram_hits_content(self):
        """trigram 检索能命中题干/答案中的中文组合词（不只标题）。"""
        rows = db.fts_search("分布式", limit=5)
        if not rows:
            self.skipTest("题库无「分布式」相关内容（数据依赖，跳过）")
        joined = "".join(
            (r["title"] or "") + (r["content"] or "") + (r["answer"] or "") for r in rows
        )
        self.assertIn("分布式", joined)

    def test_fts_search_short_keyword_fallback(self):
        """2 字符短词不触发 trigram，正常回退 unicode61/LIKE，不抛错。"""
        rows = db.fts_search("缓存", limit=3)
        self.assertIsInstance(rows, list)

    def test_browse_questions_searches_content(self):
        """题库浏览关键词走全文检索：能命中题干/答案中的中文组合词。"""
        rows = db.browse_questions(keyword="分布式", limit=5)
        if not rows:
            self.skipTest("题库无「分布式」相关内容（数据依赖，跳过）")
        joined = "".join(
            (r["title"] or "") + (r["content"] or "") + (r["answer"] or "") for r in rows
        )
        self.assertIn("分布式", joined)

    def test_browse_questions_with_filters(self):
        """关键词可与来源/难度过滤叠加，且过滤条件生效。"""
        rows = db.browse_questions(keyword="Redis", source="leetcode", limit=5)
        if not rows:
            self.skipTest("题库无 leetcode 来源的 Redis 题（数据依赖，跳过）")
        for r in rows:
            self.assertEqual(r["source"], "leetcode")

    def test_browse_questions_favorite_only(self):
        """仅看收藏：只返回已收藏题目（用后即清理，不影响真实数据）。"""
        rows = db.search_questions(limit=3)
        if not rows:
            self.skipTest("题库为空（数据依赖，跳过）")
        fav = rows[0]["id"]
        db.add_favorite(fav)
        try:
            fav_rows = db.browse_questions(favorite_only=True, limit=50)
            self.assertTrue(any(r["id"] == fav for r in fav_rows))
            for r in fav_rows:
                self.assertTrue(db.is_favorite(r["id"]))
        finally:
            db.remove_favorite(fav)

    def test_list_tags(self):
        """标签列表返回（按出现次数降序）。"""
        tags = db.list_tags()
        self.assertIsInstance(tags, list)
        if tags:
            self.assertGreater(tags[0][1], 0)

    def test_update_question_details(self):
        """详情补全：按 source+source_id 回写答案/难度（用后恢复）。"""
        row = db.search_questions(limit=1)[0]
        old_answer = row["answer"]
        try:
            n = db.update_question_details(
                row["source"], row["source_id"], answer="补全测试答案", difficulty=row["difficulty"]
            )
            self.assertEqual(n, 1)
            row2 = db.get_question_by_id(row["id"])
            self.assertEqual(row2["answer"], "补全测试答案")
        finally:
            db.update_question_details(row["source"], row["source_id"], answer=old_answer)

    def test_browse_questions_tag_filter(self):
        """标签筛选生效，且可与其他条件叠加。"""
        rows = db.browse_questions(tags=["Redis"], limit=5)
        if not rows:
            self.skipTest("题库无 Redis 标签题（数据依赖，跳过）")
        for r in rows:
            self.assertIn("Redis", r["tags"] or "")

    def test_get_question_by_id(self):
        first = db.search_questions(limit=1)[0]
        row = db.get_question_by_id(first["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], first["id"])

    def test_ask_question_by_id_switches_to_mock(self):
        """辅导模式下点「出这道题」→ 重置为模拟面试并出题（mock LLM，不真实调用）。"""
        q = db.search_questions(limit=1)[0]
        s = InterviewSession("coach")
        with mock.patch("app.agent.coach.llm.chat", return_value="好的，请听题：假设你是面试官…"):
            reply = s.ask_question_by_id(q["id"])
        self.assertEqual(s.mode, "mock")
        self.assertEqual(s.turn, "answering")
        self.assertIn(q["id"], s.asked_ids)
        self.assertTrue(reply, "应返回面试官提问")

    def test_ask_question_by_id_resets_in_mock(self):
        """模拟面试中途点「出这道题」→ 会话被重置，旧状态不残留。"""
        q = db.search_questions(limit=1)[0]
        s = InterviewSession("mock")
        s.stage_idx = 5
        s.answers = [{"stage": "旧阶段", "title": "旧题", "answer": "旧答案"}]
        with mock.patch("app.agent.coach.llm.chat", return_value="新题提问"):
            s.ask_question_by_id(q["id"])
        self.assertEqual(s.stage_idx, 0, "阶段应被重置")
        self.assertEqual(s.answers, [], "旧答案不应残留")


class BankFeatureDbTests(unittest.TestCase):
    """收藏 / 自定义题 / 公司标签：临时库测试，不依赖真实题库数据。"""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._orig_db_path = config.DB_PATH
        config.DB_PATH = Path(self._tmp_dir) / "test.db"
        db.init_db()

    def tearDown(self):
        config.DB_PATH = self._orig_db_path

    def test_favorite_roundtrip(self):
        db.upsert_question(source="custom", title="收藏测试题", answer="参考答案")
        q = db.search_questions(keyword="收藏测试题", limit=1)[0]
        self.assertTrue(db.add_favorite(q["id"]))
        self.assertFalse(db.add_favorite(q["id"]), "重复收藏应返回 False")
        self.assertTrue(db.is_favorite(q["id"]))
        self.assertEqual(len(db.list_favorites()), 1)
        db.remove_favorite(q["id"])
        self.assertFalse(db.is_favorite(q["id"]))
        self.assertEqual(len(db.list_favorites()), 0)

    def test_custom_question_with_company(self):
        db.upsert_question(
            source="custom",
            title="字节后端面试题",
            answer="参考答案",
            tags=["Redis", "限流"],
            difficulty="中等",
            company="字节跳动",
        )
        self.assertIn("字节跳动", db.list_companies())
        rows = db.search_questions(company="字节跳动")
        self.assertTrue(any(r["title"] == "字节后端面试题" for r in rows))
        self.assertEqual(db.search_questions(company="不存在的公司"), [])


class ApiAndFlowTests(unittest.TestCase):
    """多用户 REST API 与核心流程（TestClient + 临时库 + mock LLM，不依赖真实题库）。"""

    def setUp(self):
        # 隔离数据库，避免污染真实 data/questions.db
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test_api.db"
        self._patch = mock.patch.object(config, "DB_PATH", self._db_path)
        self._patch.start()
        db.init_db()
        from app import auth

        db.create_user("api_tester", auth.hash_password("pass123456"))
        self.user = db.get_user_by_username("api_tester")
        self.token = auth.issue_token(self.user["id"])
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_auth_required(self):
        """未登录访问受保护接口返回 401。"""
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            self.assertEqual(client.get("/api/session").status_code, 401)
            self.assertEqual(client.get("/api/questions").status_code, 401)
            self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_register_login_flow(self):
        """注册→自动登录→me→重复注册冲突→错误密码 401。"""
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "newbie", "password": "secret123", "nickname": "新手"},
            )
            self.assertEqual(r.status_code, 200)
            token = r.json()["token"]
            h = {"Authorization": f"Bearer {token}"}
            self.assertEqual(client.get("/api/auth/me", headers=h).json()["nickname"], "新手")
            self.assertEqual(
                client.post(
                    "/api/auth/register", json={"username": "newbie", "password": "x123456"}
                ).status_code,
                409,
            )
            self.assertEqual(
                client.post(
                    "/api/auth/login", json={"username": "newbie", "password": "wrong"}
                ).status_code,
                401,
            )

    def test_question_browse_and_favorite(self):
        """题库浏览 + 按用户收藏/取消收藏。"""
        db.upsert_question(
            source="mianshiya",
            title="什么是 GIL",
            answer="全局解释器锁",
            tags=["Python基础"],
            difficulty="简单",
        )
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            r = client.get("/api/questions", headers=self.headers)
            self.assertEqual(r.status_code, 200)
            items = r.json()["items"]
            self.assertTrue(len(items) >= 1)
            qid = items[0]["id"]
            self.assertEqual(
                client.post(f"/api/favorites/{qid}", headers=self.headers).status_code, 200
            )
            fav = client.get(
                "/api/questions", params={"favorite_only": "true"}, headers=self.headers
            ).json()
            self.assertEqual([i["id"] for i in fav["items"]], [qid])
            self.assertEqual(
                client.delete(f"/api/favorites/{qid}", headers=self.headers).status_code, 200
            )
            fav = client.get(
                "/api/questions", params={"favorite_only": "true"}, headers=self.headers
            ).json()
            self.assertEqual(fav["items"], [])

    def test_mock_empty_bank_no_crash(self):
        """题库为空（mock _pick_question 返回 None）：自我介绍→提示→再输入不崩溃。

        放在本类而非 QuestionBankChecks：该类 setUp 会 skip 空库，恰是此场景。
        """
        s = InterviewSession("mock")
        with mock.patch("app.agent.coach._pick_question", return_value=None):
            r1 = s.handle("自我介绍：我是张三，3年后端经验")
            self.assertIn("题库", r1, "应返回空题库提示")
            self.assertEqual(s.turn, "answering", "出题失败后仍停留在答题态")
            r2 = s.handle("我的回答内容")
            self.assertIn("题库", r2, "判空兜底重试出题，仍提示且不崩溃")
            self.assertFalse(s.finished)

    def test_mock_session_stream_chat(self):
        """启动模拟面试 + SSE 流式聊天（mock LLM），会话持久化可恢复。"""
        db.upsert_question(
            source="mianshiya",
            title="Python 的 GIL 是什么？",
            answer="x",
            tags=["Python基础"],
            difficulty="简单",
        )
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            r = client.post("/api/session/start", json={"mode": "mock"}, headers=self.headers)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["mode"], "mock")
            with mock.patch("app.agent.llm.chat_stream", return_value=iter(["标准", "答案"])):
                r = client.post("/api/chat", json={"message": "你好"}, headers=self.headers)
                self.assertEqual(r.status_code, 200)
                body = r.text
                self.assertIn("标准答案", body)
                self.assertIn('"type": "done"', body)
            s = client.get("/api/session", headers=self.headers).json()
            self.assertTrue(s["active"], "会话应已持久化")
            self.assertGreaterEqual(len(s["history"]), 3)

    def test_custom_status_and_start(self):
        """定制面试状态接口（按用户）与清空。"""
        import app.voice_store as voice_store
        from app.voice_server import app
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            self.assertEqual(
                client.get("/api/custom/status", headers=self.headers).json()["ready"], False
            )
            voice_store.save_custom_interview(self.user["id"], "Python 后端", "", ["Q1", "Q2"])
            st = client.get("/api/custom/status", headers=self.headers).json()
            self.assertTrue(st["ready"])
            self.assertEqual(st["job_title"], "Python 后端")
            self.assertEqual(
                client.delete("/api/custom", headers=self.headers).status_code, 200
            )
            self.assertEqual(
                client.get("/api/custom/status", headers=self.headers).json()["ready"], False
            )


if __name__ == "__main__":
    unittest.main()

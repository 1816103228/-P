"""题库浏览功能验证（标准库 unittest）。

运行：
    python -m unittest app.tests.test_question_bank -v

注意：设置 DISABLE_SCHEDULER=1 避免 AppTest 触发真实后台爬虫；
数据类测试在空库时自动跳过（先跑爬虫抓取）。
"""
import os
import unittest
from unittest import mock

os.environ["DISABLE_SCHEDULER"] = "1"

from app import db
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




class UITest(unittest.TestCase):
    """Web 界面渲染与空题库兜底检查（AppTest/状态机，不依赖真实题库）。"""

    def setUp(self):
        db.init_db()  # 兜底：确保界面可读取题库统计

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

    def test_bank_expander_renders(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file("main.py", default_timeout=60)
        at.run()
        self.assertFalse(at.exception, f"界面运行异常: {at.exception}")
        self.assertGreaterEqual(len(at.expander), 1, "应存在题库浏览 expander")
        # 按钮：开始面试 + 清空对话 + 至少一个「出这道题」（题库非空时）
        self.assertGreaterEqual(len(at.button), 2)

    def test_ask_question_from_bank_switches_to_mock_mode(self):
        """辅导答疑模式下点「出这道题」→ 模式应切回模拟面试，保证后续回答进入面试流程。"""
        from streamlit.testing.v1 import AppTest

        from app.agent.coach import InterviewSession

        at = AppTest.from_file("main.py", default_timeout=60)
        at.session_state["session"] = InterviewSession("coach")  # 预设辅导答疑会话
        at.run()
        bank = at.expander[0]
        ask_buttons = [b for b in bank.button if "ask_" in (b.key or "")]
        if not ask_buttons:
            self.skipTest("题库为空，无「出这道题」按钮")
        with mock.patch("app.agent.coach.llm.chat", return_value="好的，请听题"):
            ask_buttons[0].click()
            at.run()
        self.assertFalse(at.exception, f"界面运行异常: {at.exception}")
        # 会话已切为模拟面试；模式标记会在下一次渲染前应用到 radio
        self.assertEqual(at.session_state["session"].mode, "mock")
        self.assertEqual(at.session_state["_force_mode"], "模拟面试")
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["mode"], "模拟面试")


if __name__ == "__main__":
    unittest.main()

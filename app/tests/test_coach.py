"""教练层状态机测试：完整模拟面试流程 + 流式输出（mock LLM，不触网）。"""
import unittest
from unittest import mock

from app.agent.coach import InterviewSession, generate_interview_questions


FAKE_QUESTION = {
    "id": 1,
    "title": "谈谈 Python 的 GIL",
    "tags": "Python基础",
    "difficulty": "简单",
    "source": "mianshiya",
}


class CoachFlowTests(unittest.TestCase):
    def test_full_mock_interview_flow(self):
        s = InterviewSession("mock")
        with mock.patch("app.agent.coach._pick_question", return_value=FAKE_QUESTION), \
             mock.patch("app.agent.coach.llm.chat", return_value="小P回复"):
            s.handle("自我介绍：我是张三，3年后端")
            self.assertEqual(s.turn, "answering")
            self.assertEqual(s.stage_idx, 0)
            for i in range(6):
                s.handle("我的回答")
                self.assertEqual(s.turn, "followup", f"第{i+1}题后应进入追问")
                r = s.handle("追问的回答")
                if i < 5:
                    self.assertEqual(s.stage_idx, i + 1)
                    self.assertEqual(s.turn, "answering")
                else:
                    self.assertTrue(s.finished)
                    self.assertEqual(s.turn, "report")
                    self.assertTrue(
                        any(m["role"] == "user" and "评分" in m["content"] for m in s.messages),
                        "总结报告提示词应包含评分要求",
                    )

    def test_coach_mode_rag(self):
        s = InterviewSession("coach")
        with mock.patch("app.agent.coach.db.fts_search", return_value=[]), \
             mock.patch("app.agent.coach.llm.chat", return_value="标准参考回答…"):
            reply = s.handle("Redis 缓存穿透怎么答")
        self.assertIn("标准参考回答", reply)
        self.assertEqual(s.mode, "coach")

    def test_handle_stream_yields_text(self):
        s = InterviewSession("coach")
        with mock.patch("app.agent.coach.db.fts_search", return_value=[]), \
             mock.patch("app.agent.coach.llm.chat_stream", return_value=iter(["标", "准", "答", "案"])):
            text = "".join(s.handle_stream("怎么答"))
        self.assertEqual(text, "标准答案")
        self.assertEqual(s.messages[-1]["role"], "assistant")

    def test_input_truncated(self):
        s = InterviewSession("coach")
        long_text = "x" * 5000
        with mock.patch("app.agent.coach.db.fts_search", return_value=[]), \
             mock.patch("app.agent.coach.llm.chat", return_value="ok"):
            s.handle(long_text)
        self.assertLessEqual(len(s.messages[-2]["content"]), 4000)

    def test_custom_questions_flow(self):
        """定制题：按给定题目逐题推进，答完所有定制题后出报告。"""
        s = InterviewSession("mock", questions=["Q1", "Q2"], job_title="高级 Python 后端", jd="精通 FastAPI")
        with mock.patch("app.agent.coach.llm.chat", return_value="小P回复"):
            s.handle("自我介绍：我是张三")
            self.assertEqual(s.current_q["title"], "Q1")
            self.assertEqual(s._stage_name(), "定制题 1")
            s.handle("Q1 的答案")
            s.handle("追问答案")
            self.assertEqual(s.current_q["title"], "Q2")
            s.handle("Q2 的答案")
            s.handle("追问答案")
        self.assertTrue(s.finished)
        self.assertEqual(s.turn, "report")
        self.assertEqual([a["stage"] for a in s.answers], ["定制题 1", "定制题 2"])

    def test_generate_interview_questions_parses_list(self):
        with (
            mock.patch(
                "app.agent.coach.llm.chat",
                return_value="1. 说说 FastAPI 的依赖注入\n2. 如何设计缓存\n3. 项目难点",
            ),
            mock.patch("app.agent.coach.db.fts_search", return_value=[]),
        ):
            qs = generate_interview_questions("后端工程师", "熟悉 FastAPI")
        self.assertEqual(qs, ["说说 FastAPI 的依赖注入", "如何设计缓存", "项目难点"])


if __name__ == "__main__":
    unittest.main()

"""定制面试 Agent 测试：技术栈提取 + 题库检索 + 题目生成（mock LLM，不触网）。"""
import unittest
from unittest import mock

from app.agent import customizer


def _row(title, difficulty="中等"):
    return {"title": title, "difficulty": difficulty}


class CustomizerTests(unittest.TestCase):
    def test_extract_tech_stack_parses_json(self):
        with mock.patch(
            "app.agent.customizer.llm.chat",
            return_value='{"keywords": ["Redis", "高并发", "MySQL"]}',
        ):
            kw = customizer.extract_tech_stack("后端工程师", "熟悉 Redis 与高并发")
        self.assertEqual(kw, ["Redis", "高并发", "MySQL"])

    def test_extract_tech_stack_fallback_on_error(self):
        with mock.patch(
            "app.agent.customizer.llm.chat", side_effect=RuntimeError("boom")
        ):
            kw = customizer.extract_tech_stack("Python 后端工程师", "熟悉 FastAPI")
        self.assertTrue(kw, "失败时应回退到关键词拆分")

    def test_search_bank_dedupes_and_limits(self):
        hits = [
            _row("Redis 缓存穿透", "中等"),
            _row("Redis 持久化", "简单"),
            _row("MySQL 索引", "中等"),
        ]
        with mock.patch(
            "app.agent.customizer.db.search_questions", return_value=hits
        ):
            out = customizer.search_bank(["Redis", "MySQL"], limit=2)
        self.assertEqual([h["title"] for h in out], ["Redis 缓存穿透", "Redis 持久化"])

    def test_generate_uses_tech_and_bank(self):
        seen_prompts: list[str] = []

        def fake_chat(messages, **kw):
            seen_prompts.append(messages[0]["content"])
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["Redis", "Django"]}'
            return '{"questions": ["Q1: Redis 缓存设计", "Q2: Django ORM 优化"]}'

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch(
                "app.agent.customizer.db.search_questions",
                return_value=[_row("Redis 缓存穿透", "中等")],
            ),
        ):
            qs = customizer.generate_interview_questions("Python 后端", "熟悉 Redis/Django")
        self.assertEqual(qs, ["Q1: Redis 缓存设计", "Q2: Django ORM 优化"])
        gen_prompt = seen_prompts[1]
        self.assertIn("Redis 缓存穿透", gen_prompt, "生成 prompt 应包含题库命中的真题")
        self.assertIn("Redis", gen_prompt)

    def test_generate_fallback_plain_list(self):
        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["Redis"]}'
            return "1. 说说缓存设计\n2. 如何做限流"

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch("app.agent.customizer.db.search_questions", return_value=[]),
        ):
            qs = customizer.generate_interview_questions("后端", "")
        self.assertEqual(qs, ["说说缓存设计", "如何做限流"])

    def test_generate_empty_reply_has_fallback_question(self):
        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": []}'
            return "{}"

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch("app.agent.customizer.db.search_questions", return_value=[]),
        ):
            qs = customizer.generate_interview_questions("Java 工程师", "")
        self.assertEqual(len(qs), 1)
        self.assertIn("Java 工程师", qs[0])


if __name__ == "__main__":
    unittest.main()

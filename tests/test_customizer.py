"""定制面试 Agent 测试：技术栈提取 + 题库检索 + 题目生成（mock LLM，不触网）。"""

import unittest
from unittest import mock

from app.agent import customizer


def _row(title, difficulty="中等", answer=""):
    return {"title": title, "difficulty": difficulty, "answer": answer}


class CustomizerTests(unittest.TestCase):
    def test_extract_tech_stack_parses_json(self):
        with mock.patch(
            "app.agent.customizer.llm.chat",
            return_value='{"keywords": ["Redis", "高并发", "MySQL"]}',
        ):
            kw = customizer.extract_tech_stack("后端工程师", "熟悉 Redis 与高并发")
        self.assertEqual(kw, ["Redis", "高并发", "MySQL"])

    def test_extract_tech_stack_fallback_on_error(self):
        with mock.patch("app.agent.customizer.llm.chat", side_effect=RuntimeError("boom")):
            kw = customizer.extract_tech_stack("Python 后端工程师", "熟悉 FastAPI")
        self.assertTrue(kw, "失败时应回退到关键词拆分")

    def test_search_bank_dedupes_and_limits(self):
        hits = [
            _row("Redis 缓存穿透", "中等", answer="缓存穿透参考"),
            _row("Redis 持久化", "简单", answer="持久化参考"),
            _row("MySQL 索引", "中等", answer="索引参考"),
        ]
        with mock.patch("app.agent.customizer.db.fts_search", return_value=hits):
            out = customizer.search_bank(["Redis", "MySQL"], limit=2)
        self.assertEqual([h["title"] for h in out], ["Redis 缓存穿透", "Redis 持久化"])
        self.assertEqual(out[0]["answer"], "缓存穿透参考", "检索结果应携带参考答案")

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
                "app.agent.customizer.db.fts_search",
                return_value=[_row("Redis 缓存穿透", "中等")],
            ),
        ):
            qs = customizer.generate_interview_questions("Python 后端", "熟悉 Redis/Django")
        self.assertEqual(qs, ["Q1: Redis 缓存设计", "Q2: Django ORM 优化"])
        gen_prompt = seen_prompts[1]
        self.assertIn("Redis 缓存穿透", gen_prompt, "生成 prompt 应包含题库命中的真题")
        self.assertIn("Redis", gen_prompt)

    def test_generate_includes_answer_reference(self):
        """定制面试生成 prompt 应包含命中真题的参考答案（RAG 增强）。"""
        seen_prompts: list[str] = []

        def fake_chat(messages, **kw):
            seen_prompts.append(messages[0]["content"])
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["Redis"]}'
            return '{"questions": ["Q1: Redis 缓存设计"]}'

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch(
                "app.agent.customizer.db.fts_search",
                return_value=[_row("Redis 缓存穿透", "中等", answer="缓存穿透是指请求绕过缓存直达数据库…")],
            ),
        ):
            customizer.generate_interview_questions("后端", "Redis")
        gen_prompt = seen_prompts[1]
        self.assertIn("缓存穿透是指", gen_prompt, "生成 prompt 应包含命中真题的参考答案")
        self.assertIn("参考答案", gen_prompt)

    def test_generate_meta_sources_bank_when_hit(self):
        """命中本地真题时，来源标注为「题库」。"""

        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["Redis"]}'
            return '{"questions": ["Q1: Redis 缓存设计"]}'

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch(
                "app.agent.customizer.db.fts_search",
                return_value=[_row("Redis 缓存穿透", "中等")],
            ),
        ):
            qs, meta = customizer.generate_interview_questions_with_meta("后端", "Redis")
        self.assertEqual(meta["sources"], ["题库"])
        self.assertFalse(meta["lazy_fetched"])
        self.assertEqual(len(qs), 1)

    def test_generate_fallback_plain_list(self):
        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["Redis"]}'
            return "1. 说说缓存设计\n2. 如何做限流"

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch("app.agent.customizer.db.fts_search", return_value=[]),
            mock.patch(
                "app.agent.customizer.lazy.backfill_for_job",
                return_value={"attempted": 0, "new": 0, "detail": ""},
            ),
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
            mock.patch("app.agent.customizer.db.fts_search", return_value=[]),
            mock.patch(
                "app.agent.customizer.lazy.backfill_for_job",
                return_value={"attempted": 0, "new": 0, "detail": ""},
            ),
        ):
            qs = customizer.generate_interview_questions("Java 工程师", "")
        self.assertEqual(len(qs), 1)
        self.assertIn("Java 工程师", qs[0])

    def test_generate_triggers_lazy_backfill_on_zero_hits(self):
        """零命中时触发懒加载补抓；补抓后重新检索命中 → 标注「题库」。"""
        called: list[str] = []

        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["前端"]}'
            return '{"questions": ["Q1: Vue 生命周期", "Q2: 组件通信"]}'

        # 第一次检索零命中 → 触发懒加载（补抓 12 道）→ 第二次检索命中
        bank_results = iter([[], [_row("Vue 组件通信", "中等")]])
        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch(
                "app.agent.customizer.db.fts_search",
                side_effect=lambda kw, limit=5: next(bank_results),
            ),
            mock.patch(
                "app.agent.customizer.lazy.backfill_for_job",
                side_effect=lambda *a, **kw: (
                    called.append("backfill")
                    or {
                        "attempted": 1,
                        "new": 12,
                        "detail": "已补抓「前端」真题 12 道",
                    }
                ),
            ),
        ):
            qs, meta = customizer.generate_interview_questions_with_meta("前端开发", "Vue")
        self.assertEqual(called, ["backfill"], "零命中时应触发懒加载补抓")
        self.assertTrue(meta["lazy_fetched"])
        self.assertEqual(meta["sources"], ["题库", "题库"])
        self.assertEqual(len(qs), 2)

    def test_generate_triggers_answer_backfill(self):
        """懒加载补抓后：后台异步追答案（不阻塞出题），meta 标记 answer_backfill。"""

        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": ["前端"]}'
            return '{"questions": ["Q1: Vue 生命周期"]}'

        bank_results = iter([[], [_row("Vue 组件通信", "中等")]])
        lazy_info = {
            "attempted": 1,
            "new": 12,
            "detail": "已补抓「前端」真题 12 道",
            "source_ids": {"mianshiya": ["123", "456"]},
        }
        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch(
                "app.agent.customizer.db.fts_search",
                side_effect=lambda kw, limit=5: next(bank_results),
            ),
            mock.patch(
                "app.agent.customizer.lazy.backfill_for_job", return_value=lazy_info
            ),
            mock.patch("app.agent.customizer.lazy.enrich_answers_async") as m_enrich,
        ):
            qs, meta = customizer.generate_interview_questions_with_meta("前端开发", "Vue")
        m_enrich.assert_called_once_with("mianshiya", ["123", "456"])
        self.assertTrue(meta["answer_backfill"])
        self.assertTrue(meta["lazy_fetched"])
        self.assertEqual(len(qs), 1)

    def test_generate_zero_hits_marks_ai_source(self):
        """懒加载后仍零命中 → 来源标注「AI生成」（非真题）。"""

        def fake_chat(messages, **kw):
            if "keywords" in messages[0]["content"]:
                return '{"keywords": []}'
            return '{"questions": ["Q1: 产品需求如何分析"]}'

        with (
            mock.patch("app.agent.customizer.llm.chat", side_effect=fake_chat),
            mock.patch("app.agent.customizer.db.fts_search", return_value=[]),
            mock.patch(
                "app.agent.customizer.lazy.backfill_for_job",
                return_value={"attempted": 1, "new": 0, "detail": "未抓到该岗位对应的题库真题"},
            ),
        ):
            qs, meta = customizer.generate_interview_questions_with_meta("产品经理", "")
        self.assertFalse(meta["lazy_fetched"])
        self.assertEqual(meta["sources"], ["AI生成"])
        self.assertEqual(len(qs), 1)


if __name__ == "__main__":
    unittest.main()

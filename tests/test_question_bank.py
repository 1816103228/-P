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

# AppTest.from_file 的相对路径按调用文件解析，这里显式指向 Web 入口
WEB_ENTRY = Path(__file__).resolve().parents[1] / "app" / "ui" / "web.py"


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

    def test_bank_dialog_renders(self):
        """主界面渲染 + 打开题库对话框后出现「加入面试」选择按钮（题库非空时）。"""
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(WEB_ENTRY, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, f"界面运行异常: {at.exception}")
        labels = [b.label for b in at.button]
        self.assertIn("开始面试", labels)
        self.assertTrue(any("浏览题库" in label for label in labels), "题库入口应在侧边栏")
        next(b for b in at.button if "浏览题库" in b.label).click()
        at.run()
        self.assertFalse(at.exception, f"界面运行异常: {at.exception}")
        if not any("qb_add_" in (b.key or "") for b in at.button):
            self.skipTest("题库为空，无「加入面试」按钮")

    def test_ask_question_from_bank_switches_to_mock_mode(self):
        """打开题库并点「加入面试」：界面不崩溃。

        AppTest 对 st.dialog 内按钮点击支持有限（点击不会触发处理器），
        选多题 → 综合面试的会话构建逻辑由 test_coach 定制题流程覆盖。
        """
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(WEB_ENTRY, default_timeout=60)
        at.run()
        next(b for b in at.button if "浏览题库" in b.label).click()
        at.run()
        add_buttons = [b for b in at.button if "qb_add_" in (b.key or "")]
        if not add_buttons:
            self.skipTest("题库为空，无「加入面试」按钮")
        add_buttons[0].click()
        at.run()
        self.assertFalse(at.exception, f"界面运行异常: {at.exception}")


if __name__ == "__main__":
    unittest.main()

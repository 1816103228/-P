"""面试官小P核心逻辑：模拟面试 / 辅导答疑双模式状态机。

- 角色、规则与阶段配置集中在 app/prompts.py（单一来源）；
- 模拟面试：自我介绍 → 六阶段递进出题（每题点评+追问）→ 总结报告（0-100 评分）；
- 辅导答疑：标准参考回答 + 加分点 + 变式题，RAG 检索本地题库（FTS5）；
- 上下文管理：超出阈值自动把早期对话压缩成摘要，控制 token 成本；
- 选题：SQL 层随机（带标签/难度/排除已出题），避免全表捞回内存过滤；
- 支持流式输出（handle_stream）与同步输出（handle）。
"""
import json
import logging
import re

import app.db as db
from app import prompts
from app.agent import llm

logger = logging.getLogger("interview_coach.coach")

MAX_INPUT_CHARS = 4000          # 单次用户输入上限（防粘贴长文烧 token）
MAX_CONTEXT_MESSAGES = 24       # 上下文消息数阈值（不含 system）
SUMMARY_CHUNK = 16              # 超阈值后，每次把最旧的 N 条压缩为摘要

EMPTY_BANK_HINT = "题库暂时为空，请先运行 python -m app.crawler.run 抓取题库。"
FINISHED_HINT = "本轮模拟面试已结束。可以开始新一轮，或切换到辅导答疑模式继续练习。"
NO_REPLY_FALLBACK = "（小P暂时无法回答，请稍后重试。）"


def _sanitize_input(text: str) -> str:
    """去首尾空白 + 截断超长输入。"""
    return (text or "").strip()[:MAX_INPUT_CHARS]


def generate_interview_questions(job_title: str, jd: str, count: int = 8) -> list[str]:
    """根据目标岗位与招聘信息（JD）生成一套定制面试题。"""
    job_title = (job_title or "").strip()
    jd = (jd or "").strip()
    prompt = (
        "你是资深技术面试官，正在为一轮真实的岗位面试出题。\n"
        f"目标岗位：{job_title or '未指定（按通用后端开发）'}\n"
        f"招聘信息 / JD：\n{jd or '未提供'}\n\n"
        f"请针对该岗位生成 {count} 道递进的面试问题："
        "前 2 道为基础知识，中间考察核心技术栈与项目经验，最后 1-2 道为场景/系统设计题；"
        "题目要具体、贴近 JD 中提到的技术点。\n"
        "只输出编号列表，每行一道题，不要任何多余文字、解释或 markdown 符号。"
    )
    reply = llm.chat([{"role": "user", "content": prompt}], temperature=0.6, max_tokens=1600)
    questions: list[str] = []
    for line in reply.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[\.、)）])\s*", "", line).strip()
        if line:
            questions.append(line)
    return questions[:count] or [f"请结合你的经历谈谈对{job_title or '该岗位'}的理解"]


def _pick_question(
    stage_tags: list[str],
    source: str | None,
    difficulty: str | None,
    exclude_ids: set[int],
):
    """按条件在 SQL 层随机选题；难度/标签逐步放宽，最后全量兜底。"""
    for kwargs in (
        {"tags": stage_tags, "source": source, "difficulty": difficulty},
        {"tags": stage_tags, "source": source},
        {},
    ):
        rows = db.pick_random_question(exclude_ids=exclude_ids, **kwargs)
        if rows:
            return rows[0]
    return None


class InterviewSession:
    """一次模拟面试/辅导会话的状态。"""

    def __init__(self, mode: str, questions: list[str] | None = None, job_title: str = "", jd: str = ""):
        self.mode = mode                  # 'mock' | 'coach'
        self.stage_idx = 0                # 当前阶段下标（模拟）
        self.asked_ids: set[int] = set()  # 已出题 id
        self.messages: list[dict] = []    # LLM 完整对话历史
        self.current_q = None             # 当前题目行（sqlite3.Row 或 None）
        self.turn = "greeting"            # 模拟：greeting|answering|followup|report
        self.answers: list[dict] = []     # 记录用户答案，供评分
        self.finished = False
        self.custom_questions = questions or []

        rules = prompts.MOCK_RULES if mode == "mock" else prompts.COACH_RULES
        extra = ""
        if self.custom_questions:
            extra = (
                f"\n\n【本轮为定制面试】目标岗位：{job_title or '未知'}\n"
                f"招聘信息/JD：{(jd or '未提供')[:2000]}\n"
                f"本轮共 {len(self.custom_questions)} 道定制题，逐题推进，按流程点评与追问。"
            )
        self.messages = [{"role": "system", "content": prompts.ROLE + "\n" + rules + extra}]

    # ------------------------------------------------------------ 定制面试

    def _stage_name(self) -> str:
        if self.custom_questions and self.stage_idx < len(self.custom_questions):
            return f"定制题 {self.stage_idx + 1}"
        if self.stage_idx < len(prompts.STAGES):
            return prompts.STAGES[self.stage_idx][0]
        return "总结"

    def _total_questions(self) -> int:
        return len(self.custom_questions) if self.custom_questions else len(prompts.STAGES)

    # ------------------------------------------------------------ 对外主入口

    def handle(self, user_text: str) -> str:
        """同步入口：接收用户输入，推进状态，返回完整 AI 回复。"""
        user_text = _sanitize_input(user_text)
        if self.mode == "coach":
            return self._handle_coach(user_text)
        return self._handle_mock(user_text)

    def handle_stream(self, user_text: str):
        """流式入口：返回生成器，逐段产出回复文本（配合 st.write_stream）。"""
        user_text = _sanitize_input(user_text)
        if self.mode == "coach":
            return self._handle_coach_stream(user_text)
        return self._handle_mock_stream(user_text)

    # ------------------------------------------------------------ LLM 调用

    def _chat(self, max_tokens: int = 2048, temperature: float = 0.7) -> str:
        """统一的同步 LLM 调用（先压缩上下文）。"""
        self._maybe_compact()
        return llm.chat(self.messages, max_tokens=max_tokens, temperature=temperature)

    def _chat_stream(self, max_tokens: int = 2048, temperature: float = 0.7):
        """统一的流式 LLM 调用（先压缩上下文），返回增量迭代器。"""
        self._maybe_compact()
        return llm.chat_stream(self.messages, max_tokens=max_tokens, temperature=temperature)

    # ------------------------------------------------------------ 上下文压缩

    def _maybe_compact(self) -> None:
        """消息超阈值时，把最旧一批对话压缩成摘要，控制上下文长度。"""
        system = self.messages[0]
        rest = self.messages[1:]
        if len(rest) <= MAX_CONTEXT_MESSAGES:
            return
        to_summarize = rest[:SUMMARY_CHUNK]
        rest = rest[SUMMARY_CHUNK:]
        before = len(self.messages)
        try:
            summary = llm.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是对话压缩器。把以下面试对话压缩成 200 字以内的摘要，"
                            "保留：已问题目、用户回答要点、已给出的点评与追问。只输出摘要。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(to_summarize, ensure_ascii=False)},
                ],
                max_tokens=300,
                temperature=0.3,
            )
        except Exception:
            logger.exception("上下文压缩失败，直接丢弃最旧对话")
            summary = f"（已省略 {len(to_summarize)} 条较早对话）"
        self.messages = [
            system,
            {"role": "system", "content": f"【早期对话摘要】{summary}"},
            *rest,
        ]
        logger.info("上下文已压缩：%s -> %s 条消息", before, len(self.messages))

    # ------------------------------------------------------------ 辅导答疑

    def _build_rag_block(self, relevant) -> str:
        if not relevant:
            return ""
        block = "\n\n【参考题库（以下题目与当前问题相关，可辅助回答）】\n"
        for i, r in enumerate(relevant, 1):
            block += f"{i}. {r['title']}  [{r['source']} · {r['difficulty'] or '未知'}]\n"
            if r["answer"]:
                block += f"   参考答案：{r['answer'][:500]}\n"
        block += "\n请结合以上题库参考内容，按辅导答疑模板（标准参考回答 + 加分点 + 变式题）回答用户。若题库内容与问题无关可忽略。\n"
        return block

    def _handle_coach(self, user_text: str) -> str:
        relevant = db.fts_search(keyword=user_text, limit=5)
        rag_block = self._build_rag_block(relevant)
        self.messages.append({"role": "user", "content": rag_block + user_text})
        reply = self._chat()
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def _handle_coach_stream(self, user_text: str):
        relevant = db.fts_search(keyword=user_text, limit=5)
        rag_block = self._build_rag_block(relevant)
        self.messages.append({"role": "user", "content": rag_block + user_text})
        chunks: list[str] = []
        for delta in self._chat_stream():
            chunks.append(delta)
            yield delta
        reply = "".join(chunks).strip() or NO_REPLY_FALLBACK
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    # ------------------------------------------------------------ 模拟面试

    def _handle_mock(self, user_text: str) -> str:
        # 1) 开场：用户自我介绍后 → 出第一题
        if self.turn == "greeting":
            self.turn = "answering"
            return self._ask_next_question()

        # 2) 用户在答题 → 点评 + 追问
        if self.turn == "answering":
            if self.current_q is None:
                return self._ask_next_question()  # 题库为空兜底
            self.answers.append(
                {
                    "stage": self._stage_name(),
                    "title": self.current_q["title"],
                    "answer": user_text,
                }
            )
            self.messages.append({"role": "user", "content": f"（第{self.stage_idx+1}题我的回答）{user_text}"})
            self.messages.append(
                {"role": "user", "content": "用户刚回答了当前问题。请：1) 点评（好的方面+不足，简洁）；2) 追问 1 个深挖细节。"}
            )
            reply = self._chat()
            self.messages.append({"role": "assistant", "content": reply})
            self.turn = "followup"
            return reply

        # 3) 用户在答追问 → 进入下一题 或 结束出报告
        if self.turn == "followup":
            self.messages.append({"role": "user", "content": f"（追问的回答）{user_text}"})
            self.stage_idx += 1
            if self.stage_idx >= self._total_questions():
                return self._finish_report()
            return self._ask_next_question()

        # 4) 报告已出
        return FINISHED_HINT

    def _handle_mock_stream(self, user_text: str):
        if self.turn == "greeting":
            self.turn = "answering"
            return (yield from self._ask_next_question_stream())

        if self.turn == "answering":
            if self.current_q is None:
                return (yield from self._ask_next_question_stream())
            self.answers.append(
                {
                    "stage": self._stage_name(),
                    "title": self.current_q["title"],
                    "answer": user_text,
                }
            )
            self.messages.append({"role": "user", "content": f"（第{self.stage_idx+1}题我的回答）{user_text}"})
            self.messages.append(
                {"role": "user", "content": "用户刚回答了当前问题。请：1) 点评（好的方面+不足，简洁）；2) 追问 1 个深挖细节。"}
            )
            chunks: list[str] = []
            for delta in self._chat_stream():
                chunks.append(delta)
                yield delta
            reply = "".join(chunks).strip() or NO_REPLY_FALLBACK
            self.messages.append({"role": "assistant", "content": reply})
            self.turn = "followup"
            return reply

        if self.turn == "followup":
            self.messages.append({"role": "user", "content": f"（追问的回答）{user_text}"})
            self.stage_idx += 1
            if self.stage_idx >= self._total_questions():
                return (yield from self._finish_report_stream())
            return (yield from self._ask_next_question_stream())

        yield FINISHED_HINT
        return FINISHED_HINT

    # ------------------------------------------------------------ 内部动作

    def _ask_next_question(self) -> str:
        if self.custom_questions and self.stage_idx < len(self.custom_questions):
            stage_name = f"定制题 {self.stage_idx + 1}"
            diff = "未知"
            q = {
                "id": -(self.stage_idx + 1),
                "title": self.custom_questions[self.stage_idx],
                "tags": "定制",
                "difficulty": diff,
                "source": "定制",
            }
        else:
            stage_name, stage_tags, source, difficulty = prompts.STAGES[self.stage_idx]
            q = _pick_question(stage_tags, source, difficulty, self.asked_ids)
            if q is None:
                return EMPTY_BANK_HINT
            diff = q["difficulty"] or "未知"
        self.asked_ids.add(q["id"])
        self.current_q = q
        self.messages.append(
            {
                "role": "user",
                "content": (
                    f"【出题】第{self.stage_idx+1}题，阶段「{stage_name}」，"
                    f"难度「{diff}」。题目：{q['title']}\n"
                    f"请以面试官口吻把这道题自然地抛给用户（可稍作引导，不要直接给答案）。"
                ),
            }
        )
        reply = self._chat()
        self.messages.append({"role": "assistant", "content": reply})
        self.turn = "answering"
        return reply

    def _ask_next_question_stream(self):
        if self.custom_questions and self.stage_idx < len(self.custom_questions):
            stage_name = f"定制题 {self.stage_idx + 1}"
            diff = "未知"
            q = {
                "id": -(self.stage_idx + 1),
                "title": self.custom_questions[self.stage_idx],
                "tags": "定制",
                "difficulty": diff,
                "source": "定制",
            }
        else:
            stage_name, stage_tags, source, difficulty = prompts.STAGES[self.stage_idx]
            q = _pick_question(stage_tags, source, difficulty, self.asked_ids)
            if q is None:
                yield EMPTY_BANK_HINT
                return EMPTY_BANK_HINT
            diff = q["difficulty"] or "未知"
        self.asked_ids.add(q["id"])
        self.current_q = q
        self.messages.append(
            {
                "role": "user",
                "content": (
                    f"【出题】第{self.stage_idx+1}题，阶段「{stage_name}」，"
                    f"难度「{diff}」。题目：{q['title']}\n"
                    f"请以面试官口吻把这道题自然地抛给用户（可稍作引导，不要直接给答案）。"
                ),
            }
        )
        chunks: list[str] = []
        for delta in self._chat_stream():
            chunks.append(delta)
            yield delta
        reply = "".join(chunks).strip() or NO_REPLY_FALLBACK
        self.messages.append({"role": "assistant", "content": reply})
        self.turn = "answering"
        return reply

    def _finish_report(self) -> str:
        self.turn = "report"
        self.finished = True
        answers_txt = "\n".join(
            f"- [{a['stage']}] {a['title']}\n  回答：{a['answer'][:300]}" for a in self.answers
        )
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "全部题目已结束。请基于以下用户回答，输出【总结报告】：\n"
                    f"1) 表现评分（0-100，按 {prompts.SCORE_WEIGHTS} 加权，给出分项分）；\n"
                    "2) 知识薄弱点（具体到知识点）；\n"
                    "3) 改进建议清单（可执行、分优先级）。\n\n"
                    f"用户全部回答：\n{answers_txt}"
                ),
            }
        )
        reply = self._chat(max_tokens=3000)
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def _finish_report_stream(self):
        self.turn = "report"
        self.finished = True
        answers_txt = "\n".join(
            f"- [{a['stage']}] {a['title']}\n  回答：{a['answer'][:300]}" for a in self.answers
        )
        self.messages.append(
            {
                "role": "user",
                "content": (
                    "全部题目已结束。请基于以下用户回答，输出【总结报告】：\n"
                    f"1) 表现评分（0-100，按 {prompts.SCORE_WEIGHTS} 加权，给出分项分）；\n"
                    "2) 知识薄弱点（具体到知识点）；\n"
                    "3) 改进建议清单（可执行、分优先级）。\n\n"
                    f"用户全部回答：\n{answers_txt}"
                ),
            }
        )
        chunks: list[str] = []
        for delta in self._chat_stream(max_tokens=3000):
            chunks.append(delta)
            yield delta
        reply = "".join(chunks).strip() or NO_REPLY_FALLBACK
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def reset(self, mode: str) -> None:
        """重建会话状态（模式切换/中途切题时调用）。"""
        self.__init__(mode)

    def ask_question_by_id(self, qid: int) -> str:
        """题库浏览→「出这道题」：直接以指定题目出题，开启一段新的模拟面试。"""
        row = db.get_question_by_id(qid)
        if row is None:
            return "题目不存在，可能已被清理。"
        self.reset("mock")  # 无条件重置，避免旧 stage/answers/messages 残留
        self.current_q = row
        self.asked_ids.add(row["id"])
        diff = row["difficulty"] or "未知"
        self.messages.append(
            {
                "role": "user",
                "content": (
                    f"【出题】用户从题库主动选择了这道题，来源「{row['source']}」，"
                    f"难度「{diff}」。题目：{row['title']}\n"
                    f"请以面试官口吻把这道题自然地抛给用户（可稍作引导，不要直接给答案）。"
                ),
            }
        )
        reply = self._chat()
        self.messages.append({"role": "assistant", "content": reply})
        self.turn = "answering"
        return reply

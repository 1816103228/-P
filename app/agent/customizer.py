"""定制面试 Agent：分析 JD 技术栈 → 检索本地题库 → 生成贴近岗位的定制题。

轻量函数管线（非独立服务/进程）：文字版与语音版共用同一入口，
一处升级两边同时生效。入口保持 generate_interview_questions(job_title, jd)。
"""
import json
import logging
import re

import app.db as db
from app.agent import llm

logger = logging.getLogger("interview_coach.customizer")


def _parse_json_object(reply: str) -> dict | None:
    """从回复文本中截取第一个 JSON 对象；解析失败返回 None。"""
    try:
        start = reply.find("{")
        end = reply.rfind("}")
        if start >= 0 and end > start:
            return json.loads(reply[start : end + 1])
        return json.loads(reply)
    except Exception:
        return None


def _fallback_keywords(job_title: str, jd: str) -> list[str]:
    """LLM 提取失败时，按常见分隔符拆分岗位/JD 作为关键词兜底。"""
    text = f"{job_title} {jd}"
    seen: set[str] = set()
    out: list[str] = []
    for token in re.split(r"[，,。；;、/\s（）()【】\[\]：:]+", text):
        token = token.strip()
        if len(token) >= 2 and token not in seen:
            seen.add(token)
            out.append(token)
        if len(out) >= 8:
            break
    return out


def extract_tech_stack(job_title: str, jd: str) -> list[str]:
    """从目标岗位与 JD 提取核心技术栈关键词（供题库检索）。"""
    prompt = (
        "你是 JD 解析器。从目标岗位与招聘信息中提取核心技术栈关键词，"
        "例如：Redis、MySQL、Docker、Kafka、高并发、消息队列、算法。\n"
        f"目标岗位：{job_title or '未指定'}\n招聘信息：\n{jd or '未提供'}\n\n"
        '只输出 JSON：{"keywords": ["...", "..."]}，不超过 8 个，不要多余文字。'
    )
    try:
        reply = llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
    except Exception:
        logger.exception("技术栈提取失败，回退关键词拆分")
        return _fallback_keywords(job_title, jd)
    data = _parse_json_object(reply)
    keywords = data.get("keywords") if data else None
    if isinstance(keywords, list):
        cleaned = [str(k).strip() for k in keywords if str(k).strip()]
        if cleaned:
            return cleaned[:8]
    return _fallback_keywords(job_title, jd)


def search_bank(keywords: list[str], limit: int = 8) -> list[dict]:
    """按技术栈关键词检索本地题库，返回去重后的 {title, difficulty} 列表。"""
    seen: set[str] = set()
    hits: list[dict] = []
    for kw in keywords:
        for row in db.search_questions(keyword=kw, limit=5):
            title = str(row["title"]).strip()
            if not title or title in seen:
                continue
            seen.add(title)
            hits.append({"title": title, "difficulty": row["difficulty"] or "未知"})
            if len(hits) >= limit:
                return hits
    return hits


def _parse_questions(reply: str, count: int) -> list[str] | None:
    """解析题目：优先 JSON，兼容旧版纯文本编号列表。"""
    data = _parse_json_object(reply)
    if data is not None:
        if isinstance(data.get("questions"), list):
            out: list[str] = []
            for item in data["questions"]:
                if isinstance(item, str):
                    out.append(item.strip())
                elif isinstance(item, dict):
                    out.append(str(item.get("question") or item.get("title") or "").strip())
            out = [q for q in out if q]
            if out:
                return out[:count]
        # 是合法 JSON 但没给出题目 → 不当作纯文本解析
        return None
    out = []
    for line in reply.splitlines():
        line = re.sub(r"^\s*(?:[-*]|\d+[\.、)）])\s*", "", line).strip()
        if line:
            out.append(line)
    return out[:count] or None


def generate_interview_questions(job_title: str, jd: str, count: int = 8) -> list[str]:
    """定制面试 Agent：提取技术栈 → 检索题库 → 生成贴近岗位的定制题。"""
    job_title = (job_title or "").strip()
    jd = (jd or "").strip()
    tech = extract_tech_stack(job_title, jd)
    bank_hits = search_bank(tech)
    bank_block = "\n".join(f"- {h['title']}（{h['difficulty']}）" for h in bank_hits)
    prompt = (
        "你是资深技术面试官，正在为一轮真实的岗位面试出题。\n"
        f"目标岗位：{job_title or '未指定（按通用后端开发）'}\n"
        f"招聘信息 / JD：\n{jd or '未提供'}\n"
        f"识别出的技术栈：{', '.join(tech) or '未识别'}\n"
        f"本地题库中可参考的真题（可选用或改写，不要照搬编号）：\n{bank_block or '（暂无匹配）'}\n\n"
        f"请针对该岗位生成 {count} 道递进的面试问题："
        "前 2 道为基础知识，中间考察核心技术栈与项目经验，最后 1-2 道为场景/系统设计题；"
        "题目要具体、贴近 JD 中提到的技术点。\n"
        '只输出 JSON：{"questions": ["题目1", "题目2", "..."]}，不要多余文字。'
    )
    reply = llm.chat(
        [{"role": "user", "content": prompt}],
        temperature=0.6,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    questions = _parse_questions(reply, count)
    return questions or [f"请结合你的经历谈谈对{job_title or '该岗位'}的理解"]

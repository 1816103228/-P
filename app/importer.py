"""批量导入自定义题库：CSV 文本 → db.upsert_many（纯函数，离线可测）。"""
import csv
import io
import re

import app.db as db

#: 表头别名（顺序即列含义）；无表头时按位置：题干,答案,标签,难度,公司
_HEADER_ALIASES = {
    "title": ("题目", "题干", "标题", "问题"),
    "answer": ("答案", "参考答案"),
    "tags": ("标签",),
    "difficulty": ("难度",),
    "company": ("公司",),
}
_DIFFICULTY = {"简单", "中等", "困难"}


def _split_tags(value: str) -> list[str]:
    return [t.strip() for t in re.split(r"[，,、;；/]", value or "") if t.strip()]


def _pick_fields(row: list[str], header: list[str] | None):
    padded = [c.strip() for c in row] + [""] * 8
    if header:

        def get(aliases: tuple[str, ...]) -> str:
            for name in aliases:
                if name in header:
                    return padded[header.index(name)]
            return ""

        return (
            get(_HEADER_ALIASES["title"]),
            get(_HEADER_ALIASES["answer"]),
            get(_HEADER_ALIASES["tags"]),
            get(_HEADER_ALIASES["difficulty"]),
            get(_HEADER_ALIASES["company"]),
        )
    return padded[0], padded[1], padded[2], padded[3], padded[4]


def parse_questions_csv(text: str) -> list[dict]:
    """解析 CSV（支持表头或纯数据），返回 upsert_many 可直接使用的 dict 列表。"""
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return []
    header = None
    first = [c.strip() for c in rows[0]]
    if any(c in _HEADER_ALIASES["title"] for c in first):
        header = first
        rows = rows[1:]
    questions: list[dict] = []
    for row in rows:
        title, answer, tags, difficulty, company = _pick_fields(row, header)
        if not title:
            continue
        questions.append(
            {
                "source": "custom",
                "title": title,
                "answer": answer or None,
                "tags": _split_tags(tags) or None,
                "difficulty": difficulty if difficulty in _DIFFICULTY else None,
                "company": company or None,
            }
        )
    return questions


def import_questions_csv(text: str) -> dict:
    """导入 CSV 自定义题库，返回统计 {'new': n, 'skipped': n, 'rows': n}。"""
    questions = parse_questions_csv(text)
    if not questions:
        return {"new": 0, "skipped": 0, "rows": 0}
    stats = db.upsert_many(questions)
    stats["rows"] = len(questions)
    return stats

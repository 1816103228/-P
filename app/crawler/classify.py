"""语义清洗引擎：用 DeepSeek 批量给标签粗糙的题打标（供 app.crawler.clean 调用）。

清洗入口已收敛到统一清洗层 app/crawler/clean.py（clean_status 状态机）；
本模块只保留 LLM 打标的引擎部分（标签池 / 提示词 / 批处理 / 解析重试）。

用法（推荐走统一入口）：
    python -m app.crawler.clean --semantic       # 语义清洗增量
    python -m app.crawler.clean --semantic --dry # 预览，不写库
"""

import argparse
import json
import logging
import re

from app import config, db
from app.agent.llm import chat

logger = logging.getLogger("interview_coach.crawler.classify")

# 标准标签池（与模拟面试六阶段对应）
KNOWN_TAGS = [
    "Python基础",
    "数据结构",
    "算法-排序",
    "算法-动态规划",
    "算法-双指针",
    "算法-树图",
    "算法-贪心",
    "算法-二分",
    "算法-回溯",
    "算法-数学",
    "算法-其他",
    "数据库",
    "Redis",
    "网络-HTTP",
    "网络-TCP",
    "网络-其他",
    "操作系统",
    "并发",
    "系统设计",
    "项目经验",
    "设计模式",
    "框架工具",
    "其他",
]

BATCH_SIZE = 15  # 每批送 LLM 的题数（减少 API 调用）

CLASSIFY_PROMPT = f"""你是面试题库分类助手。以下是 {BATCH_SIZE} 道后端面试题，请给每道题打 1-3 个分类标签。

=== 可用标签 ===
{", ".join(KNOWN_TAGS)}

=== 规则 ===
- 算法题看标题中的算法名和题号：LeetCode 题按题型分（排序/DP/双指针/树图/贪心/二分/回溯/数学/其他），非算法题优先按知识点分
- 浏览器的题里有「TCP」「HTTP」等协议词的，用 网络-TCP 或 网络-HTTP
- 涉及 Redis、MySQL 的用专门标签；通用数据库问题用「数据库」
- 涉及多线程/锁/协程的用「并发」
- 涉及架构设计的用「系统设计」
- 同一题可以有多个标签

=== 输出格式 ===
只输出一个 JSON 对象（不要 markdown 包裹），格式：
{{"items": [{{"index": 0, "tags": ["标签1"]}}, ...]}}

=== 题目列表 ===
"""


def _extract_items(text: str) -> list | None:
    """从 LLM 回复中提取 items 列表（兼容对象与数组两种格式）。"""
    # 优先解析 {"items": [...]}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(obj, dict) and isinstance(obj.get("items"), list):
                return obj["items"]
    # 兼容旧格式：直接输出数组
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(arr, list):
                return arr
    return None


def classify_batch(questions: list[dict]) -> list[list[str]] | None:
    """给一批题打标签，返回按 index 排序的 tags 列表；解析失败重试一次。"""
    batch = [{"index": i, "title": q["title"]} for i, q in enumerate(questions)]
    prompt = CLASSIFY_PROMPT + json.dumps({"questions": batch}, ensure_ascii=False)
    parsed = None
    for attempt in (1, 2):
        reply = chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        parsed = _extract_items(reply)
        if parsed is not None:
            break
        logger.warning("第 %s 次解析失败，LLM 回复: %s", attempt, reply[:200])
        prompt += "\n\n上次输出无法解析，请严格只输出 JSON，不要任何其他文字。"

    if parsed is None:
        return None

    result: list[list[str]] = [[] for _ in questions]
    for item in parsed:
        if not isinstance(item, dict) or "index" not in item:
            continue
        idx = item["index"]
        if 0 <= idx < len(result):
            result[idx] = [t for t in item.get("tags", []) if t in KNOWN_TAGS]
            if not result[idx]:
                result[idx] = ["其他"]
    return result


def classify_all(limit: int | None = None, dry_run: bool = False) -> None:
    """对标签粗糙/缺失的题做语义清洗（委托统一清洗层，增量推进）。"""
    from app.crawler import clean

    print(f"正在语义清洗（单次最多 {config.CLEAN_SEMANTIC_BATCH_LIMIT} 条）…")
    stats = clean.run_semantic_clean(limit=limit, dry_run=dry_run)
    print(stats)
    print("清洗统计:", clean.clean_stats())


if __name__ == "__main__":
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="预览模式，不写数据库")
    parser.add_argument("--limit", type=int, default=None, help="最多标注条数")
    args = parser.parse_args()
    classify_all(limit=args.limit, dry_run=args.dry)

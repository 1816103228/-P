"""题库分类标注：用 DeepSeek 批量给未分类的题打标签。

用法：
    python -m app.crawler.classify          # 全量标注
    python -m app.crawler.classify --dry     # 预览，不写库
    python -m app.crawler.classify --limit 50  # 只标前 50 条

优化：要求 json_object 结构化输出；解析失败自动重试一次并追加纠错提示。
"""
import argparse
import json
import logging
import re
import time

from app import db
from app.agent.llm import chat

logger = logging.getLogger("interview_coach.crawler.classify")

# 标准标签池（与模拟面试六阶段对应）
KNOWN_TAGS = [
    "Python基础", "数据结构",
    "算法-排序", "算法-动态规划", "算法-双指针", "算法-树图",
    "算法-贪心", "算法-二分", "算法-回溯", "算法-数学", "算法-其他",
    "数据库", "Redis",
    "网络-HTTP", "网络-TCP", "网络-其他",
    "操作系统", "并发",
    "系统设计", "项目经验", "设计模式", "框架工具",
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
    """对题库中所有未分类的题打标签（已有细标签的跳过）。"""
    conn = db.get_conn()
    try:
        rows = conn.execute(
            """SELECT id, title, tags FROM questions
               WHERE tags IS NULL
                  OR tags IN ('算法,数据结构', '后端', '')
                  OR (source = 'mianshiya' AND tags NOT LIKE '%Python基础%')
               ORDER BY fetched_at
            """
        ).fetchall()
    finally:
        conn.close()

    if limit:
        rows = rows[:limit]

    total = len(rows)
    if total == 0:
        print("没有需要标注的题目。")
        return

    print(f"待标注题目：{total} 条（每批 {BATCH_SIZE} 条，约 {total // BATCH_SIZE + 1} 批）\n")

    done = 0
    for i in range(0, total, BATCH_SIZE):
        batch_rows = rows[i : i + BATCH_SIZE]
        batch_dicts = [{"title": r["title"]} for r in batch_rows]
        tags_list = classify_batch(batch_dicts)
        if tags_list is None:
            print("  [警告] 本批解析失败，跳过（可重跑）")
            continue

        for row, tags in zip(batch_rows, tags_list):
            if tags:
                done += 1
                tag_str = ",".join(tags)
                if dry_run:
                    print(f"  [{row['id']}] {row['title'][:60]} → {tag_str}")
                else:
                    conn = db.get_conn()
                    try:
                        conn.execute("UPDATE questions SET tags=? WHERE id=?", (tag_str, row["id"]))
                        conn.commit()
                    finally:
                        conn.close()

        print(f"  进度: {min(i + BATCH_SIZE, total)}/{total} (已标注 {done})")
        time.sleep(0.5)  # 避免触发 API 限流

    print(f"\n完成！共标注 {done} 道题" + (" [预览模式，未写库]" if dry_run else ""))


if __name__ == "__main__":
    db.init_db()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="预览模式，不写数据库")
    parser.add_argument("--limit", type=int, default=None, help="最多标注条数")
    args = parser.parse_args()
    classify_all(limit=args.limit, dry_run=args.dry)

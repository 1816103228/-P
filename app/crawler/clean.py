"""统一数据清洗层：把散落在各处的清洗逻辑收敛到此处，按 clean_status 状态机批量执行。

状态流转：
    raw ──规则清洗──> rule_cleaned ──语义清洗(LLM)──> semantic_cleaned
                      │（标签已达标，无需语义）
                      └───────────────────> ready

清洗分层：
- 结构层清洗：在数据源适配器抓取时完成（源特定格式 → 统一 schema），不在此处；
- 规则清洗（本地、免费、快）：标题清理、答案去噪/截断、标签去重、难度校验；
  CLEAN_RULE_VERSION 升级后只重洗 clean_version 过期的行（精准重洗，省成本）；
- 语义清洗（LLM、增量、按批次）：只处理规则清洗后标签仍粗糙/缺失的行，
  CLEAN_SEMANTIC_BATCH_LIMIT 控制单次打标量，避免一次烧太多 API 成本。

用法：
    python -m app.crawler.clean               # 规则 + 语义增量清洗
    python -m app.crawler.clean --rule        # 只跑规则清洗
    python -m app.crawler.clean --semantic    # 只跑语义清洗
    python -m app.crawler.clean --stats       # 只打印清洗统计
    python -m app.crawler.clean --dry --limit 50  # 预览，不写库
"""

import argparse
import logging
import re

from app import config, db

logger = logging.getLogger("interview_coach.crawler.clean")

#: 规则版本号（写入 clean_version）；升级后旧版本数据会被精准重洗
CLEAN_VERSION = config.CLEAN_RULE_VERSION

#: 答案最大长度（超长截断，避免刷屏与 token 浪费）
ANSWER_MAX = 2000
#: 难度合法值
_DIFFICULTIES = {"简单", "中等", "困难"}
#: 标题开头的题号噪音（如 "1. "、"42. "）
_TITLE_NOISE = re.compile(r"^\d+[.\s]+")
#: 标签里的界面噪音（如 "VIP"）
_TAG_NOISE = {"VIP"}
#: 粗糙标签（未细化，需语义清洗才能打准）
COARSE_TAGS = {"算法", "数据结构", "后端", "算法,数据结构", "Python", "前端", "测试", "运维"}


def clean_title(title: str) -> str:
    """清理标题：去首部题号与空白。"""
    return _TITLE_NOISE.sub("", (title or "").strip()).strip()


def clean_answer(answer: str | None) -> str | None:
    """通用答案清洗：去首尾空白、空值转 None、超长截断。"""
    a = (answer or "").strip()
    if not a:
        return None
    return a[:ANSWER_MAX]


def clean_tags(tags: list[str] | None) -> list[str] | None:
    """标签清洗：去空白/去界面噪音/去重；结果为空返回 None。"""
    if not tags:
        return None
    out: list[str] = []
    for t in tags:
        t = (t or "").strip()
        if not t or t in _TAG_NOISE:
            continue
        if t not in out:
            out.append(t)
    return out or None


def clean_difficulty(difficulty: str | None) -> str:
    """难度校验：非法/空值回填「中等」。"""
    d = (difficulty or "").strip()
    return d if d in _DIFFICULTIES else "中等"


def needs_semantic(tags_str: str | None) -> bool:
    """标签是否粗糙/缺失，需要 LLM 语义打标。"""
    t = (tags_str or "").strip()
    return t in COARSE_TAGS or t == ""


def _clean_row(row) -> dict:
    """对单行应用规则清洗，返回需要回写的字段（无需改动的不返回）。"""
    updates: dict = {}
    title = clean_title(row["title"])
    if title and title != row["title"]:
        updates["title"] = title
    answer = clean_answer(row["answer"])
    if answer != row["answer"]:
        updates["answer"] = answer
    tags = clean_tags((row["tags"] or "").split(",") if row["tags"] else None)
    new_tags = ",".join(tags) if tags else None
    if new_tags != row["tags"]:
        updates["tags"] = new_tags
    difficulty = clean_difficulty(row["difficulty"])
    if difficulty != row["difficulty"]:
        updates["difficulty"] = difficulty
    return updates


def run_rule_clean(limit: int | None = None, dry_run: bool = False) -> dict:
    """规则清洗：处理 raw 及 clean_version 过期的行；标签达标直接 ready，否则 rule_cleaned。"""
    rows = db.list_pending_rule_clean(CLEAN_VERSION, limit=limit)
    ready_ids: list[int] = []
    rule_ids: list[int] = []
    updated = 0
    for row in rows:
        updates = _clean_row(row)
        if updates and not dry_run:
            db.update_question_fields(row["id"], updates)
            updated += 1
        if needs_semantic(updates.get("tags", row["tags"])):
            rule_ids.append(row["id"])
        else:
            ready_ids.append(row["id"])
    if not dry_run:
        if rule_ids:
            db.mark_clean(rule_ids, db.CLEAN_STATUS_RULE, CLEAN_VERSION)
        if ready_ids:
            db.mark_clean(ready_ids, db.CLEAN_STATUS_READY, CLEAN_VERSION)
    return {
        "scanned": len(rows),
        "fields_updated": updated,
        "rule_cleaned": len(rule_ids),
        "ready": len(ready_ids),
    }


def run_semantic_clean(
    limit: int | None = None,
    dry_run: bool = False,
    progress=None,
) -> dict:
    """语义清洗（LLM 打标）：只处理标签粗糙/缺失的题，按批次增量推进。"""
    from app.crawler import classify

    limit = limit if limit is not None else config.CLEAN_SEMANTIC_BATCH_LIMIT
    rows = db.list_pending_semantic_clean(coarse_tags=COARSE_TAGS, limit=limit)
    if not rows:
        return {"scanned": 0, "labeled": 0, "skipped": 0}
    labeled_ids: list[int] = []
    tags_by_id: dict[int, str] = {}
    for i in range(0, len(rows), classify.BATCH_SIZE):
        batch = rows[i : i + classify.BATCH_SIZE]
        tags_list = classify.classify_batch([{"title": r["title"]} for r in batch])
        if tags_list is None:
            logger.warning("语义清洗本批解析失败，跳过（可重跑）")
            continue
        for row, tags in zip(batch, tags_list, strict=False):
            if not tags:
                continue
            tagged = ",".join(tags)
            if dry_run:
                print(f"  [{row['id']}] {row['title'][:40]} → {tagged}")
            labeled_ids.append(row["id"])
            tags_by_id[row["id"]] = tagged
        if progress:
            progress(min(i + classify.BATCH_SIZE, len(rows)), len(rows))
    if not dry_run and labeled_ids:
        db.set_question_tags(labeled_ids, tags_by_id, CLEAN_VERSION)
    return {"scanned": len(rows), "labeled": len(labeled_ids), "skipped": len(rows) - len(labeled_ids)}


def run_clean(
    rule_limit: int | None = None,
    semantic_limit: int | None = None,
    dry_run: bool = False,
    progress=None,
) -> dict:
    """统一清洗编排：先规则清洗，再语义清洗（增量）。返回两阶段统计。"""
    rule = run_rule_clean(limit=rule_limit, dry_run=dry_run)
    semantic = run_semantic_clean(limit=semantic_limit, dry_run=dry_run, progress=progress)
    return {"rule": rule, "semantic": semantic}


def clean_stats() -> dict:
    """清洗状态统计（覆盖率可见）。"""
    return db.clean_stats()


def main() -> None:
    """命令行入口：按参数执行规则/语义/统计清洗。"""
    parser = argparse.ArgumentParser(description="题库统一清洗")
    parser.add_argument("--rule", action="store_true", help="只跑规则清洗")
    parser.add_argument("--semantic", action="store_true", help="只跑语义清洗")
    parser.add_argument("--stats", action="store_true", help="只打印清洗统计")
    parser.add_argument("--dry", action="store_true", help="预览，不写库")
    parser.add_argument("--limit", type=int, default=None, help="每阶段最多处理条数")
    args = parser.parse_args()

    db.init_db()
    if args.stats:
        print(clean_stats())
        return
    if args.semantic:
        stats = run_semantic_clean(limit=args.limit, dry_run=args.dry)
    elif args.rule:
        stats = run_rule_clean(limit=args.limit, dry_run=args.dry)
    else:
        stats = run_clean(rule_limit=args.limit, semantic_limit=args.limit, dry_run=args.dry)
    print(stats)
    print("清洗统计:", clean_stats())


if __name__ == "__main__":
    main()

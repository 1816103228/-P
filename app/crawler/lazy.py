"""懒加载补抓：定制面试检索零命中时，按岗位映射抓取对应分类真题入库。

设计要点：
- 只抓列表页（标题/难度/标签，秒级），答案留给定时全量抓取补全；
- fetched_categories 表记录已抓分类，避免重复抓取；
- 数据源可扩展：JOB_CATEGORY_MAP 条目为 (关键词, 源名, 源分类参数, 展示名)，
  将来接入其他网页源时，在 _get_adapter 里登记新源即可。
"""

import logging
import threading
import time

from app import config, db

logger = logging.getLogger("interview_coach.crawler.lazy")

#: 岗位/JD/技术栈关键词 → 数据源分类。优先覆盖现有面试鸭技术分类；
#: 前端/测试/运维等分类面试鸭若不存在，抓取会返回空，自然回退到 AI 生成。
JOB_CATEGORY_MAP: list[tuple[str, str, str, str]] = [
    ("python", "mianshiya", "python", "Python"),
    ("后端", "mianshiya", "backend", "后端"),
    ("java", "mianshiya", "backend", "后端"),
    ("go", "mianshiya", "backend", "后端"),
    ("数据库", "mianshiya", "database", "数据库"),
    ("mysql", "mianshiya", "mysql", "MySQL"),
    ("redis", "mianshiya", "redis", "Redis"),
    ("计算机网络", "mianshiya", "computerNetwork", "计算机网络"),
    ("操作系统", "mianshiya", "os", "操作系统"),
    ("算法", "mianshiya", "algorithm", "算法"),
    ("消息队列", "mianshiya", "mq", "消息队列"),
    ("kafka", "mianshiya", "mq", "消息队列"),
    ("中间件", "mianshiya", "middleware", "中间件"),
    ("微服务", "mianshiya", "microservice", "微服务"),
    ("docker", "mianshiya", "docker", "Docker"),
    ("kubernetes", "mianshiya", "kubernetes", "Kubernetes"),
    ("k8s", "mianshiya", "kubernetes", "Kubernetes"),
    ("前端", "mianshiya", "frontend", "前端"),
    ("vue", "mianshiya", "frontend", "前端"),
    ("react", "mianshiya", "frontend", "前端"),
    ("测试", "mianshiya", "testing", "测试"),
    ("运维", "mianshiya", "ops", "运维"),
]

#: 懒加载只抓列表页前几页（每页 20 题），保证秒级返回
LAZY_PAGES = config.LAZY_CRAWL_PAGES
#: 单分类最多入库条数（控制体量与耗时）
LAZY_LIMIT = config.LAZY_CRAWL_LIMIT


def _get_adapter(source: str):
    """按源名取适配器实例；未登记的新源返回 None。"""
    if source == "mianshiya":
        from app.crawler.mianshiya import MianShiYaAdapter

        return MianShiYaAdapter()
    return None


def resolve_categories(job_title: str, jd: str, keywords: list[str]) -> list[tuple[str, str, str]]:
    """岗位/JD/技术栈关键词 → [(源名, 分类, 展示名)]，去重保序。"""
    # 拼接关键词并统一小写，便于不区分大小写地匹配
    text = f"{job_title or ''} {jd or ''} {' '.join(keywords or [])}".lower()
    hits: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kw, source, cat, label in JOB_CATEGORY_MAP:
        # 命中且未记录过才加入，避免同一分类重复
        if kw.lower() in text and (source, cat) not in seen:
            seen.add((source, cat))
            hits.append((source, cat, label))
    return hits


def _fetch_category(source: str, category: str) -> list[dict]:
    """抓单个分类列表页并入库，返回本次抓到的行。"""
    adapter = _get_adapter(source)
    if adapter is None:
        logger.warning("未知数据源 %s，跳过懒加载", source)
        return []
    # 只抓列表页，答案留给定时全量抓取补全
    rows = adapter.fetch_category(category, pages=LAZY_PAGES, limit=LAZY_LIMIT)
    # 有数据才入库
    if rows:
        db.upsert_many(rows)
    return rows


def backfill_for_job(
    job_title: str,
    jd: str,
    keywords: list[str],
    progress=None,
    timeout_seconds: float | None = None,
) -> dict:
    """零命中时按岗位补抓真题入库。

    返回 {'attempted': 尝试抓取的分类数, 'new': 新入库数, 'detail': 给用户的说明,
          'source_ids': {源名: [source_id, ...]}}（本次补抓到的题，供后台追答案）。
    全程不抛异常：任何源失败都跳过，保证定制面试流程不中断。
    """
    result = {"attempted": 0, "new": 0, "detail": "", "source_ids": {}}
    # 解析要抓的分类
    cats = resolve_categories(job_title, jd, keywords)
    if not cats:
        result["detail"] = "未识别到可补抓的分类"
        return result

    # 总超时控制
    timeout = config.LAZY_CRAWL_TIMEOUT if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + max(timeout, 1.0)
    fetched_labels: list[str] = []
    for source, cat, label in cats:
        # 超时提前停止
        if time.monotonic() > deadline:
            logger.warning("懒加载补抓超时，提前停止")
            break
        # 已抓过的分类跳过
        if db.is_category_fetched(source, cat):
            continue  # 已抓过，跳过
        # 通知用户当前在抓哪个分类
        if progress:
            progress(f"本地题库暂无匹配，正在全力抓取「{label}」真题…")
        # 单分类失败只跳过，不影响其他分类
        try:
            rows = _fetch_category(source, cat)
        except Exception as e:
            logger.warning("懒加载抓取 %s/%s 失败: %s", source, cat, e)
            continue
        if not rows:
            # 无数据：标记已抓避免反复试，回退 AI 生成
            db.mark_category_fetched(source, cat, 0)
            continue
        # 成功：记录已抓并累计统计
        db.mark_category_fetched(source, cat, len(rows))
        result["attempted"] += 1
        result["new"] += len(rows)
        # 收集本次补抓到的 source_id，供后台追答案
        result["source_ids"].setdefault(source, []).extend(
            r["source_id"] for r in rows if r.get("source_id")
        )
        fetched_labels.append(label)

    # 生成给用户的说明文案
    if result["new"]:
        result["detail"] = (
            "已补抓「" + "、".join(fetched_labels) + "」真题 " + str(result["new"]) + " 道"
        )
    else:
        result["detail"] = "未抓到该岗位对应的题库真题"
    return result


def enrich_answers(source: str, source_ids: list[str]) -> dict:
    """同步抓详情补答案：把已入库但缺答案的题补全（供后台线程调用）。"""
    if not source_ids:
        return {"total": 0, "updated": 0}
    adapter = _get_adapter(source)
    if adapter is None or not hasattr(adapter, "fetch_details_for"):
        logger.warning("数据源 %s 不支持追答案，跳过", source)
        return {"total": len(source_ids), "updated": 0, "error": "unsupported"}
    return adapter.fetch_details_for(source_ids)


def enrich_answers_async(source: str, source_ids: list[str]) -> bool:
    """后台线程追答案：不阻塞出题/面试，答题期间答案异步补全。

    尽力而为：线程异常只记日志；即使进程结束未跑完，定时全量抓取仍会兜底补全。
    """
    if not source_ids:
        return False

    def _run() -> None:
        try:
            stats = enrich_answers(source, source_ids)
            logger.info("后台补答案完成 %s %s: %s", source, len(source_ids), stats)
        except Exception:
            logger.exception("后台补答案失败 %s", source)

    threading.Thread(target=_run, name=f"enrich-{source}", daemon=True).start()
    return True

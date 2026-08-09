"""懒加载补抓：定制面试检索零命中时，按岗位映射抓取对应分类真题入库。

设计要点：
- 只抓列表页（标题/难度/标签，秒级），答案留给定时全量抓取补全；
- fetched_categories 表记录已抓分类，避免重复抓取；
- 数据源可扩展：JOB_CATEGORY_MAP 条目为 (关键词, 源名, 源分类参数, 展示名)，
  将来接入其他网页源时，在 _get_adapter 里登记新源即可。
"""

import logging
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
    text = f"{job_title or ''} {jd or ''} {' '.join(keywords or [])}".lower()
    hits: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kw, source, cat, label in JOB_CATEGORY_MAP:
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
    rows = adapter.fetch_category(category, pages=LAZY_PAGES, limit=LAZY_LIMIT)
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

    返回 {'attempted': 尝试抓取的分类数, 'new': 新入库数, 'detail': 给用户的说明}。
    全程不抛异常：任何源失败都跳过，保证定制面试流程不中断。
    """
    result = {"attempted": 0, "new": 0, "detail": ""}
    cats = resolve_categories(job_title, jd, keywords)
    if not cats:
        result["detail"] = "未识别到可补抓的分类"
        return result

    timeout = config.LAZY_CRAWL_TIMEOUT if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + max(timeout, 1.0)
    fetched_labels: list[str] = []
    for source, cat, label in cats:
        if time.monotonic() > deadline:
            logger.warning("懒加载补抓超时，提前停止")
            break
        if db.is_category_fetched(source, cat):
            continue  # 已抓过，跳过
        if progress:
            progress(f"本地题库暂无匹配，正在全力抓取「{label}」真题…")
        try:
            rows = _fetch_category(source, cat)
        except Exception as e:
            logger.warning("懒加载抓取 %s/%s 失败: %s", source, cat, e)
            continue
        if not rows:
            # 分类不存在/无数据：记录避免反复试，仍回退 AI 生成
            db.mark_category_fetched(source, cat, 0)
            continue
        db.mark_category_fetched(source, cat, len(rows))
        result["attempted"] += 1
        result["new"] += len(rows)
        fetched_labels.append(label)

    if result["new"]:
        result["detail"] = (
            "已补抓「" + "、".join(fetched_labels) + "」真题 " + str(result["new"]) + " 道"
        )
    else:
        result["detail"] = "未抓到该岗位对应的题库真题"
    return result

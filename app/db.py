"""SQLite 数据层：建库、题目去重入库、多条件检索、FTS5 全文检索。

设计要点：
- 去重：content_hash（归一化 SHA-256）唯一，INSERT OR IGNORE 增量入库；
- 连接：每次操作显式关闭（`with closing(...) as conn, conn:`），并设置 busy_timeout；
- 批量写入：executemany + 单事务，避免逐行提交（全量抓取从 ~3.5 分钟降到数秒级）；
- 全文检索：FTS5 外部内容表（标题/题干/答案/标签），不可用时回退 LIKE；
- 迁移：PRAGMA user_version 管理 schema 版本。
"""

import hashlib
import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from app import config

logger = logging.getLogger("interview_coach.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,              -- 来源标识：mianshiya / leetcode / nowcoder / ...
    source_id     TEXT,                       -- 源站题号/页面ID（便于反查）
    title         TEXT NOT NULL,              -- 题干（短问题）
    content       TEXT,                       -- 详细题干/描述
    answer        TEXT,                       -- 参考答案（源站如有，可为空）
    tags          TEXT,                       -- 逗号分隔的标签，如 "Python,GIL"
    difficulty    TEXT,                       -- 难度：简单/中等/困难（或算法题 easy/medium/hard）
    url           TEXT,                       -- 来源链接
    content_hash  TEXT NOT NULL UNIQUE,       -- 归一化去重哈希
    fetched_at    TEXT NOT NULL               -- 抓取时间（ISO 8601）
);

CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source);
CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions(tags);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);
"""

#: FTS5 外部内容表：与 questions 通过 rowid 关联，触发器保持同步
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts USING fts5(
    title, content, answer, tags,
    content='questions',
    content_rowid='id',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS questions_ai AFTER INSERT ON questions BEGIN
    INSERT INTO questions_fts(rowid, title, content, answer, tags)
    VALUES (new.id, new.title, new.content, new.answer, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS questions_ad AFTER DELETE ON questions BEGIN
    INSERT INTO questions_fts(questions_fts, rowid, title, content, answer, tags)
    VALUES ('delete', old.id, old.title, old.content, old.answer, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS questions_au AFTER UPDATE ON questions BEGIN
    INSERT INTO questions_fts(questions_fts, rowid, title, content, answer, tags)
    VALUES ('delete', old.id, old.title, old.content, old.answer, old.tags);
    INSERT INTO questions_fts(rowid, title, content, answer, tags)
    VALUES (new.id, new.title, new.content, new.answer, new.tags);
END;
"""

_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


def get_conn() -> sqlite3.Connection:
    """获取数据库连接（开启 busy_timeout 与 WAL 友好配置）。"""
    config.ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=config.DB_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {config.DB_TIMEOUT_SECONDS * 1000}")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    """建库建表 + 执行迁移（幂等，可反复调用）。"""
    with closing(get_conn()) as conn, conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """基于 PRAGMA user_version 的版本迁移。"""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        try:
            conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError as e:
            logger.warning("FTS5 不可用（%s），全文检索将回退 LIKE 检索", e)
        conn.execute("PRAGMA user_version = 1")
        logger.info("数据库迁移至版本 1：新增 FTS5 全文索引")
    _sync_fts(conn)


def _sync_fts(conn: sqlite3.Connection) -> None:
    """FTS 行数与主表不一致时重建索引（外部内容表需手动同步）。"""
    try:
        n = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        f = conn.execute("SELECT COUNT(*) FROM questions_fts").fetchone()[0]
    except sqlite3.OperationalError:
        return
    if n != f:
        conn.execute("INSERT INTO questions_fts(questions_fts) VALUES('rebuild')")
        logger.info("已重建 FTS 索引（%s -> %s）", f, n)


def _normalize(text: str) -> str:
    """归一化：去空白/换行/大小写，用于生成稳定的内容哈希。"""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def make_hash(*parts: str) -> str:
    """由题干片段组合生成去重哈希。"""
    raw = "|".join(_normalize(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _escape_like(text: str) -> str:
    """转义 LIKE 通配符，防止用户输入 %/_ 干扰匹配。"""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def upsert_question(
    *,
    source: str,
    title: str,
    source_id: str | None = None,
    content: str | None = None,
    answer: str | None = None,
    tags: list[str] | None = None,
    difficulty: str | None = None,
    url: str | None = None,
) -> bool:
    """插入一条题目，返回是否为新入库（False 表示已存在被跳过）。"""
    h = make_hash(source, title, content or "")
    now = datetime.now(timezone.utc).isoformat()
    tag_str = ",".join(tags) if tags else None
    with closing(get_conn()) as conn, conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO questions
               (source, source_id, title, content, answer, tags, difficulty, url, content_hash, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (source, source_id, title, content, answer, tag_str, difficulty, url, h, now),
        )
        return cur.rowcount > 0


def upsert_many(questions: list[dict]) -> dict:
    """批量入库（单事务），返回统计 {'new': n, 'skipped': n}。"""
    if not questions:
        return {"new": 0, "skipped": 0}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for q in questions:
        h = make_hash(q.get("source", ""), q.get("title", ""), q.get("content") or "")
        rows.append(
            (
                q.get("source", ""),
                q.get("source_id"),
                q.get("title"),
                q.get("content"),
                q.get("answer"),
                ",".join(q["tags"]) if q.get("tags") else None,
                q.get("difficulty"),
                q.get("url"),
                h,
                now,
            )
        )
    with closing(get_conn()) as conn, conn:
        cur = conn.executemany(
            """INSERT OR IGNORE INTO questions
               (source, source_id, title, content, answer, tags, difficulty, url, content_hash, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        new = max(cur.rowcount, 0)
    return {"new": new, "skipped": len(rows) - new}


def count_questions() -> int:
    with closing(get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM questions").fetchone()["n"]


def count_by_source() -> list[sqlite3.Row]:
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT source, COUNT(*) AS n FROM questions GROUP BY source ORDER BY n DESC"
        ).fetchall()


def search_questions(
    tags: list[str] | None = None,
    difficulty: str | None = None,
    source: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """按标签/难度/来源/标题关键词检索题目。"""
    sql = "SELECT * FROM questions WHERE 1=1"
    params: list = []
    if tags:
        conds = []
        for t in tags:
            conds.append("tags LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(t)}%")
        sql += " AND (" + " OR ".join(conds) + ")"
    if difficulty:
        sql += " AND difficulty = ?"
        params.append(difficulty)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if keyword:
        sql += " AND title LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(keyword)}%")
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params.append(limit)
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def pick_random_question(
    tags: list[str] | None = None,
    difficulty: str | None = None,
    source: str | None = None,
    exclude_ids: set[int] | None = None,
    limit: int = 1,
) -> list[sqlite3.Row]:
    """按条件在 SQL 层随机选题，避免全表捞回内存过滤。"""
    sql = "SELECT id, title, tags, difficulty, source FROM questions WHERE 1=1"
    params: list = []
    if tags:
        conds = []
        for t in tags:
            conds.append("tags LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(t)}%")
        sql += " AND (" + " OR ".join(conds) + ")"
    if difficulty:
        sql += " AND difficulty = ?"
        params.append(difficulty)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if exclude_ids:
        placeholders = ",".join("?" * len(exclude_ids))
        sql += f" AND id NOT IN ({placeholders})"
        params.extend(exclude_ids)
    sql += " ORDER BY RANDOM() LIMIT ?"
    params.append(limit)
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def get_question_by_id(qid: int):
    """按 id 取单条题目（题库浏览→出这道题 用）。"""
    with closing(get_conn()) as conn:
        return conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()


def latest_questions(source: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
    """最新的题目（模拟面试候选题库）。"""
    sql = "SELECT * FROM questions"
    params: list = []
    if source:
        sql += " WHERE source = ?"
        params.append(source)
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params.append(limit)
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def _fts_query(keyword: str) -> str:
    """把用户关键词转成 FTS5 MATCH 短语查询（引号转义 + AND 连接）。"""
    tokens = [t for t in _FTS_TOKEN_RE.findall(keyword) if t.strip()]
    if not tokens:
        return f'"{_escape_fts(keyword)}"'
    return " AND ".join(f'"{_escape_fts(t)}"' for t in tokens)


def _escape_fts(text: str) -> str:
    return text.replace('"', '""')


def fts_search(keyword: str, limit: int = 5) -> list[sqlite3.Row]:
    """FTS5 全文检索（标题/题干/答案/标签）；FTS 不可用或无命中时回退 LIKE。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    query = _fts_query(keyword)
    sql = """SELECT q.* FROM questions q
             JOIN questions_fts f ON q.id = f.rowid
             WHERE questions_fts MATCH ?
             ORDER BY bm25(questions_fts) LIMIT ?"""
    try:
        with closing(get_conn()) as conn:
            rows = conn.execute(sql, (query, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    if rows:
        return rows
    return search_questions(keyword=keyword, limit=limit)

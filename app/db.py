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
    company       TEXT,                       -- 公司维度标签（如 字节跳动 / 阿里 / 腾讯）
    url           TEXT,                       -- 来源链接
    content_hash  TEXT NOT NULL UNIQUE,       -- 归一化去重哈希
    fetched_at    TEXT NOT NULL               -- 抓取时间（ISO 8601）
);

CREATE INDEX IF NOT EXISTS idx_questions_source ON questions(source);
CREATE INDEX IF NOT EXISTS idx_questions_tags ON questions(tags);
CREATE INDEX IF NOT EXISTS idx_questions_difficulty ON questions(difficulty);

CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mode        TEXT NOT NULL,              -- mock / coach
    job_title   TEXT,                       -- 定制面试目标岗位
    jd          TEXT,                       -- 定制面试招聘信息
    source      TEXT,                       -- 题库 / 定制
    persona     TEXT,                       -- 面试官人格（一面/二面/三面）
    started_at  TEXT NOT NULL,              -- 开始时间（ISO 8601）
    score       INTEGER,                    -- 报告总分（0-100）
    report      TEXT,                       -- 总结报告全文
    weak_points TEXT                        -- 薄弱点清单（每行一条）
);

CREATE TABLE IF NOT EXISTS session_answers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    stage          TEXT,                    -- 阶段名（如 Python基础 / 定制题 1）
    question_title TEXT,
    answer         TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_answers_sid ON session_answers(session_id);

CREATE TABLE IF NOT EXISTS favorites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id),
    created_at  TEXT NOT NULL
);
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

#: trigram 全文索引：按 3 字符子串建索引，中文子串/组合词匹配远好于 unicode61 逐字索引。
#: 要求查询词 ≥3 字符（短词由 _fts_search 回退到 unicode61 / LIKE）。
FTS_TRIGRAM_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS questions_fts_tr USING fts5(
    title, content, answer, tags,
    content='questions',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS questions_tr_ai AFTER INSERT ON questions BEGIN
    INSERT INTO questions_fts_tr(rowid, title, content, answer, tags)
    VALUES (new.id, new.title, new.content, new.answer, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS questions_tr_ad AFTER DELETE ON questions BEGIN
    INSERT INTO questions_fts_tr(questions_fts_tr, rowid, title, content, answer, tags)
    VALUES ('delete', old.id, old.title, old.content, old.answer, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS questions_tr_au AFTER UPDATE ON questions BEGIN
    INSERT INTO questions_fts_tr(questions_fts_tr, rowid, title, content, answer, tags)
    VALUES ('delete', old.id, old.title, old.content, old.answer, old.tags);
    INSERT INTO questions_fts_tr(rowid, title, content, answer, tags)
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
    if version < 2:
        conn.execute("PRAGMA user_version = 2")
        logger.info("数据库迁移至版本 2：新增面试记录表（sessions / session_answers）")
    if version < 3:
        q_cols = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
        if "company" not in q_cols:
            conn.execute("ALTER TABLE questions ADD COLUMN company TEXT")
        s_cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
        if "persona" not in s_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN persona TEXT")
        conn.execute("PRAGMA user_version = 3")
        logger.info("数据库迁移至版本 3：新增收藏表、题目公司标签与面试官人格")
    if version < 4:
        try:
            conn.executescript(FTS_TRIGRAM_SCHEMA)
            conn.execute("INSERT INTO questions_fts_tr(questions_fts_tr) VALUES('rebuild')")
        except sqlite3.OperationalError as e:
            logger.warning("FTS5 trigram 索引创建失败（%s），中文子串检索将回退", e)
        conn.execute("PRAGMA user_version = 4")
        logger.info("数据库迁移至版本 4：新增 trigram 全文索引（中文子串检索）")
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
    try:
        t = conn.execute("SELECT COUNT(*) FROM questions_fts_tr").fetchone()[0]
        if n != t:
            conn.execute("INSERT INTO questions_fts_tr(questions_fts_tr) VALUES('rebuild')")
            logger.info("已重建 trigram FTS 索引（%s -> %s）", t, n)
    except sqlite3.OperationalError:
        pass


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
    company: str | None = None,
    url: str | None = None,
) -> bool:
    """插入一条题目，返回是否为新入库（False 表示已存在被跳过）。"""
    h = make_hash(source, title, content or "")
    now = datetime.now(timezone.utc).isoformat()
    tag_str = ",".join(tags) if tags else None
    with closing(get_conn()) as conn, conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO questions
               (source, source_id, title, content, answer, tags, difficulty, company, url, content_hash, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (source, source_id, title, content, answer, tag_str, difficulty, company, url, h, now),
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
                q.get("company"),
                q.get("url"),
                h,
                now,
            )
        )
    with closing(get_conn()) as conn, conn:
        cur = conn.executemany(
            """INSERT OR IGNORE INTO questions
               (source, source_id, title, content, answer, tags, difficulty, company, url, content_hash, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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


def list_tags() -> list[tuple[str, int]]:
    """返回全部标签及出现次数（按次数降序，供筛选下拉框使用）。"""
    counts: dict[str, int] = {}
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT tags FROM questions WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    for r in rows:
        for t in (r["tags"] or "").split(","):
            t = t.strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)


def search_questions(
    tags: list[str] | None = None,
    difficulty: str | None = None,
    source: str | None = None,
    company: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """按标签/难度/来源/公司/标题关键词检索题目。"""
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
    if company:
        sql += " AND company = ?"
        params.append(company)
    if keyword:
        sql += " AND title LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(keyword)}%")
    sql += " ORDER BY fetched_at DESC LIMIT ?"
    params.append(limit)
    with closing(get_conn()) as conn:
        return conn.execute(sql, params).fetchall()


def browse_questions(
    keyword: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    difficulty: str | None = None,
    company: str | None = None,
    favorite_only: bool = False,
    limit: int = 30,
) -> list[sqlite3.Row]:
    """题库浏览检索：关键词走 FTS5（trigram → unicode61），可叠加来源/难度/公司过滤；
    全部失败时回退 标题/题干/答案/标签 LIKE。"""
    where: list[str] = []
    params: list = []
    if source:
        where.append("q.source = ?")
        params.append(source)
    if difficulty:
        where.append("q.difficulty = ?")
        params.append(difficulty)
    if company:
        where.append("q.company = ?")
        params.append(company)
    if tags:
        conds = []
        for t in tags:
            conds.append("q.tags LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(t)}%")
        where.append("(" + " OR ".join(conds) + ")")
    if favorite_only:
        where.append("q.id IN (SELECT question_id FROM favorites)")
    cond = (" AND " + " AND ".join(where)) if where else ""
    kw = (keyword or "").strip()
    if not kw:
        sql = f"SELECT q.* FROM questions q WHERE 1=1{cond} ORDER BY q.fetched_at DESC LIMIT ?"
        params.append(limit)
        with closing(get_conn()) as conn:
            return conn.execute(sql, params).fetchall()

    # 1) trigram 命中（中文子串/组合词，≥3 字符）
    trig_q = _fts_trigram_query(kw)
    if trig_q:
        sql = (
            "SELECT q.* FROM questions q JOIN questions_fts_tr f ON q.id = f.rowid "
            f"WHERE questions_fts_tr MATCH ?{cond} ORDER BY bm25(questions_fts_tr) LIMIT ?"
        )
        try:
            with closing(get_conn()) as conn:
                rows = conn.execute(sql, [trig_q, *params, limit]).fetchall()
            if rows:
                return rows
        except sqlite3.OperationalError:
            pass
    # 2) unicode61 命中
    uq = _fts_query(kw)
    sql = (
        "SELECT q.* FROM questions q JOIN questions_fts f ON q.id = f.rowid "
        f"WHERE questions_fts MATCH ?{cond} ORDER BY bm25(questions_fts) LIMIT ?"
    )
    try:
        with closing(get_conn()) as conn:
            rows = conn.execute(sql, [uq, *params, limit]).fetchall()
        if rows:
            return rows
    except sqlite3.OperationalError:
        pass
    # 3) LIKE 兜底：标题/题干/答案/标签任一包含
    like = f"%{_escape_like(kw)}%"
    sql = (
        "SELECT q.* FROM questions q WHERE "
        "(q.title LIKE ? ESCAPE '\\' OR q.content LIKE ? ESCAPE '\\' "
        "OR q.answer LIKE ? ESCAPE '\\' OR q.tags LIKE ? ESCAPE '\\')"
        f"{cond} ORDER BY q.fetched_at DESC LIMIT ?"
    )
    with closing(get_conn()) as conn:
        return conn.execute(sql, [like, like, like, like, *params, limit]).fetchall()


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


def list_companies() -> list[str]:
    """题库中已有的公司标签（去重，按名称排序）。"""
    with closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT DISTINCT company FROM questions "
            "WHERE company IS NOT NULL AND company != '' ORDER BY company"
        ).fetchall()
    return [r["company"] for r in rows]


def create_session(
    mode: str,
    job_title: str = "",
    jd: str = "",
    source: str = "",
    persona: str = "",
    started_at: str | None = None,
) -> int:
    """创建一条面试记录，返回 session_id。"""
    started_at = started_at or datetime.now(timezone.utc).isoformat()
    with closing(get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO sessions (mode, job_title, jd, source, persona, started_at) VALUES (?,?,?,?,?,?)",
            (mode, job_title or None, jd or None, source or None, persona or None, started_at),
        )
        return cur.lastrowid


def add_session_answers(session_id: int, answers: list[dict]) -> None:
    """批量写入一轮面试的问答记录。"""
    if not answers:
        return
    rows = [(session_id, a.get("stage"), a.get("title"), a.get("answer")) for a in answers]
    with closing(get_conn()) as conn, conn:
        conn.executemany(
            "INSERT INTO session_answers (session_id, stage, question_title, answer) "
            "VALUES (?,?,?,?)",
            rows,
        )


def finish_session(
    session_id: int, score: int | None, report: str, weak_points: str | None
) -> None:
    """面试结束后回填评分、报告与薄弱点。"""
    with closing(get_conn()) as conn, conn:
        conn.execute(
            "UPDATE sessions SET score=?, report=?, weak_points=? WHERE id=?",
            (score, report, weak_points, session_id),
        )


def list_sessions(limit: int = 50) -> list[sqlite3.Row]:
    """已完成的面试记录（按开始时间倒序，供侧边栏复盘）。"""
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE report IS NOT NULL ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


def get_session_answers(session_id: int) -> list[sqlite3.Row]:
    """按会话取逐题问答记录。"""
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM session_answers WHERE session_id=? ORDER BY id", (session_id,)
        ).fetchall()


def add_favorite(question_id: int) -> bool:
    """收藏题目，返回是否为新收藏（已收藏返回 False）。"""
    with closing(get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO favorites (question_id, created_at) VALUES (?, ?)",
            (question_id, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount > 0


def remove_favorite(question_id: int) -> None:
    """取消收藏。"""
    with closing(get_conn()) as conn, conn:
        conn.execute("DELETE FROM favorites WHERE question_id = ?", (question_id,))


def is_favorite(question_id: int) -> bool:
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE question_id = ?", (question_id,)
        ).fetchone()
        return row is not None


def update_question_details(
    source: str,
    source_id: str,
    *,
    answer: str | None = None,
    difficulty: str | None = None,
) -> int:
    """按 source+source_id 更新题目答案/难度（详情页补全用），返回受影响行数。"""
    sets: list[str] = []
    params: list = []
    if answer is not None:
        sets.append("answer = ?")
        params.append(answer)
    if difficulty is not None:
        sets.append("difficulty = ?")
        params.append(difficulty)
    if not sets:
        return 0
    params += [source, source_id]
    sql = f"UPDATE questions SET {', '.join(sets)} WHERE source = ? AND source_id = ?"

    def _run() -> int:
        with closing(get_conn()) as conn, conn:
            return conn.execute(sql, params).rowcount

    try:
        return _run()
    except sqlite3.DatabaseError:
        # FTS 外部内容表与主表 rowid 错位时，UPDATE 触发器会报 malformed：重建索引后重试
        logger.warning("更新题目详情触发 FTS 异常，重建全文索引后重试")
        _rebuild_fts()
        return _run()


def _rebuild_fts() -> None:
    """重建 FTS5 索引（外部内容表 rowid 与主表错位时用于修复）。"""
    with closing(get_conn()) as conn, conn:
        for t in ("questions_fts", "questions_fts_tr"):
            try:
                conn.execute(f"INSERT INTO {t}({t}) VALUES('rebuild')")
            except sqlite3.OperationalError:
                pass


def list_favorites(limit: int = 50) -> list[sqlite3.Row]:
    """收藏的题目列表（含收藏时间，按收藏先后倒序）。"""
    with closing(get_conn()) as conn:
        return conn.execute(
            "SELECT q.*, f.created_at AS faved_at FROM favorites f "
            "JOIN questions q ON q.id = f.question_id ORDER BY f.id DESC LIMIT ?",
            (limit,),
        ).fetchall()


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


def _fts_trigram_query(keyword: str) -> str | None:
    """trigram 查询串：要求所有词都 ≥3 字符（trigram 不支持短词），否则返回 None。"""
    tokens = [t for t in _FTS_TOKEN_RE.findall(keyword) if t.strip()]
    if not tokens or any(len(t) < 3 for t in tokens):
        return None
    return " AND ".join(f'"{_escape_fts(t)}"' for t in tokens)


def _fts_trigram_search(keyword: str, limit: int = 5) -> list[sqlite3.Row]:
    """trigram 索引检索：中文子串/组合词匹配（≥3 字符），按 bm25 排序。"""
    query = _fts_trigram_query(keyword)
    if not query:
        return []
    sql = """SELECT q.* FROM questions q
             JOIN questions_fts_tr f ON q.id = f.rowid
             WHERE questions_fts_tr MATCH ?
             ORDER BY bm25(questions_fts_tr) LIMIT ?"""
    try:
        with closing(get_conn()) as conn:
            return conn.execute(sql, (query, limit)).fetchall()
    except sqlite3.OperationalError:
        return []


def fts_search(keyword: str, limit: int = 5) -> list[sqlite3.Row]:
    """全文检索：trigram（中文子串，≥3 字）→ unicode61 → LIKE 三级回退。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    rows = _fts_trigram_search(keyword, limit)
    if rows:
        return rows
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

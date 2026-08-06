"""共享 UI 组件：小P 虚拟人物头像、侧边栏题库统计（供 main.py 使用）。"""
import streamlit as st

import app.db as db


def avatar_svg(size: int = 64) -> str:
    """小P 虚拟人物头像（SVG 字符串，尺寸可调，带眨眼动画）。"""
    scale = size / 160.0
    height = int(216 * scale)
    return f'''<svg width="{size}" height="{height}" viewBox="0 0 160 216" xmlns="http://www.w3.org/2000/svg">
        <defs>
            <radialGradient id="face" cx="48%" cy="35%"><stop offset="0%" stop-color="#ffead4"/><stop offset="55%" stop-color="#f5cfaa"/><stop offset="100%" stop-color="#d4a574"/></radialGradient>
            <radialGradient id="cheek" cx="50%" cy="50%"><stop offset="0%" stop-color="#f0a0a0" stop-opacity=".5"/><stop offset="100%" stop-color="#f0a0a0" stop-opacity="0"/></radialGradient>
            <linearGradient id="suit" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#3a4f63"/><stop offset="100%" stop-color="#1e2c3a"/></linearGradient>
            <linearGradient id="tie" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#e74c3c"/><stop offset="100%" stop-color="#a93226"/></linearGradient>
            <linearGradient id="hair" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2c3e50"/><stop offset="100%" stop-color="#1a252f"/></linearGradient>
        </defs>
        <rect x="26" y="105" width="108" height="80" rx="14" fill="url(#suit)"/>
        <path d="M50,104 L80,130 L110,104" fill="#f8f9fa" stroke="url(#suit)" stroke-width="1"/>
        <polygon points="80,106 66,138 80,162 94,138" fill="url(#tie)"/>
        <rect x="10" y="118" width="20" height="50" rx="10" fill="url(#suit)"/>
        <rect x="130" y="118" width="20" height="50" rx="10" fill="url(#suit)"/>
        <ellipse cx="20" cy="170" rx="11" ry="13" fill="#f5cfaa"/>
        <ellipse cx="140" cy="170" rx="11" ry="13" fill="#f5cfaa"/>
        <rect x="66" y="95" width="28" height="16" rx="6" fill="#eac09e"/>
        <ellipse cx="80" cy="60" rx="48" ry="56" fill="url(#face)"/>
        <ellipse cx="30" cy="62" rx="8" ry="14" fill="#eac09e"/><ellipse cx="130" cy="62" rx="8" ry="14" fill="#eac09e"/>
        <path d="M32,45 Q32,0 80,-4 Q128,0 128,45 Q128,28 80,24 Q32,28 32,45" fill="url(#hair)"/>
        <path d="M34,30 Q56,16 80,14 Q104,16 126,30" fill="#3d566e" opacity=".6"/>
        <ellipse cx="48" cy="75" rx="14" ry="9" fill="url(#cheek)"/><ellipse cx="112" cy="75" rx="14" ry="9" fill="url(#cheek)"/>
        <rect x="42" y="48" width="32" height="20" rx="6" fill="#f8f9fa" stroke="#2c3e50" stroke-width="2.2"/><rect x="86" y="48" width="32" height="20" rx="6" fill="#f8f9fa" stroke="#2c3e50" stroke-width="2.2"/>
        <line x1="34" y1="58" x2="42" y2="58" stroke="#2c3e50" stroke-width="2"/><line x1="118" y1="58" x2="126" y2="58" stroke="#2c3e50" stroke-width="2"/>
        <line x1="74" y1="57" x2="86" y2="57" stroke="#2c3e50" stroke-width="2"/>
        <ellipse class="av-eye" cx="58" cy="57" rx="6" ry="7" fill="#1a1a2e"/><circle cx="60" cy="54" r="2" fill="#fff"/>
        <ellipse class="av-eye r" cx="102" cy="57" rx="6" ry="7" fill="#1a1a2e"/><circle cx="104" cy="54" r="2" fill="#fff"/>
        <path d="M44,42 Q58,36 68,44" fill="none" stroke="#1a1a2e" stroke-width="2.5" stroke-linecap="round"/><path d="M92,44 Q102,36 116,42" fill="none" stroke="#1a1a2e" stroke-width="2.5" stroke-linecap="round"/>
        <path d="M66,85 Q80,92 94,85" fill="none" stroke="#1a1a2e" stroke-width="2.3" stroke-linecap="round"/>
        <ellipse cx="60" cy="30" rx="18" ry="8" fill="#fff" opacity=".1"/>
    </svg>'''


def _stat_card(value: int, label: str) -> None:
    st.markdown(
        f'<div class="stat-card"><div class="stat-num">{value}</div>'
        f'<div class="stat-label">{label}</div></div>',
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    """侧边栏：题库统计、面试复盘（简洁卡片）+ 常用提示。"""
    db.init_db()  # 幂等：确保表存在（禁用调度器时也安全）
    total = db.count_questions()
    by_src = {r["source"]: r["n"] for r in db.count_by_source()}
    with st.sidebar:
        st.markdown('<div class="side-title">题库</div>', unsafe_allow_html=True)
        c1, c2, c3 = st.columns(3)
        with c1:
            _stat_card(total, "总题数")
        with c2:
            _stat_card(by_src.get("leetcode", 0), "LeetCode")
        with c3:
            _stat_card(by_src.get("mianshiya", 0), "面试鸭")
        st.divider()
        _render_review()
        st.divider()
        st.markdown('<div class="side-note">📅 题库每日自动更新</div>', unsafe_allow_html=True)
        st.markdown('<div class="side-note">📞 右下角语音通话（Chrome / Edge）</div>', unsafe_allow_html=True)


def _render_review() -> None:
    """面试复盘：评分走势 + 最近记录（数据层异常时静默降级）。"""
    try:
        sessions = db.list_sessions(limit=10)
    except Exception:
        return
    st.markdown('<div class="side-title">复盘</div>', unsafe_allow_html=True)
    scores = [r["score"] for r in sessions if r["score"] is not None]
    if scores:
        st.line_chart(scores, height=96)
    for r in sessions[:5]:
        score = f"{r['score']}/100" if r["score"] is not None else "—"
        kind = "定制" if r["source"] == "定制" else "题库"
        if r["persona"]:
            kind += f" · {r['persona']}"
        ts = (r["started_at"] or "")[5:16].replace("T", " ")
        st.markdown(
            f'<div class="rev-row"><span class="rev-score">{score}</span>'
            f'<span class="rev-meta">{ts} · {kind}</span></div>',
            unsafe_allow_html=True,
        )
    if not sessions:
        st.markdown('<div class="side-note">暂无面试记录</div>', unsafe_allow_html=True)

"""共享 UI 组件：小P 虚拟人物头像、侧边栏题库统计（供 main.py 使用）。"""
import streamlit as st

import app.db as db


def avatar_svg(size: int = 64) -> str:
    """小P 虚拟人物头像（可爱的女性面试官，SVG 字符串，尺寸可调，带眨眼动画）。"""
    scale = size / 160.0
    height = int(216 * scale)
    return f'''<svg width="{size}" height="{height}" viewBox="0 0 160 216" xmlns="http://www.w3.org/2000/svg">
        <defs>
            <radialGradient id="face" cx="48%" cy="35%"><stop offset="0%" stop-color="#ffe9dc"/><stop offset="55%" stop-color="#fbd2b8"/><stop offset="100%" stop-color="#e8b291"/></radialGradient>
            <radialGradient id="cheek" cx="50%" cy="50%"><stop offset="0%" stop-color="#ff9db0" stop-opacity=".55"/><stop offset="100%" stop-color="#ff9db0" stop-opacity="0"/></radialGradient>
            <linearGradient id="blouse" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fff4f6"/><stop offset="100%" stop-color="#fbd3dc"/></linearGradient>
            <linearGradient id="hair" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#a4715a"/><stop offset="100%" stop-color="#6f4533"/></linearGradient>
        </defs>
        <!-- 长发（后，顺滑收窄） -->
        <path d="M36,42 Q33,4 80,-4 Q127,4 124,42 Q126,72 118,102 Q108,130 92,138 Q85,141 80,141 Q75,141 68,138 Q52,130 42,102 Q34,72 36,42" fill="url(#hair)"/>
        <!-- 上衣（粉色，修身） -->
        <rect x="34" y="104" width="92" height="80" rx="16" fill="url(#blouse)"/>
        <path d="M58,104 L80,130 L102,104" fill="#ffffff" stroke="#f2b8c2" stroke-width="1"/>
        <!-- 蝴蝶结 -->
        <polygon points="80,105 70,112 70,125 80,118 90,125 90,112" fill="#ff8fab"/>
        <circle cx="80" cy="112.5" r="2.8" fill="#e76f8f"/>
        <!-- 袖子 -->
        <rect x="20" y="116" width="20" height="46" rx="10" fill="url(#blouse)"/>
        <rect x="120" y="116" width="20" height="46" rx="10" fill="url(#blouse)"/>
        <!-- 手 -->
        <ellipse cx="30" cy="166" rx="10" ry="12" fill="#fbd2b8"/>
        <ellipse cx="130" cy="166" rx="10" ry="12" fill="#fbd2b8"/>
        <!-- 脖子 -->
        <rect x="69" y="90" width="22" height="18" rx="5" fill="#f5c9a8"/>
        <!-- 脸（鹅蛋形） -->
        <ellipse cx="80" cy="58" rx="42" ry="56" fill="url(#face)"/>
        <!-- 耳朵 -->
        <ellipse cx="36" cy="60" rx="7" ry="13" fill="#f5c9a8"/><ellipse cx="124" cy="60" rx="7" ry="13" fill="#f5c9a8"/>
        <!-- 刘海（顺滑） -->
        <path d="M36,42 Q36,4 80,-4 Q124,4 124,42 Q122,27 110,21 Q100,17 92,25 Q84,31 76,26 Q66,22 56,28 Q45,22 36,42" fill="url(#hair)"/>
        <!-- 侧发（贴脸） -->
        <path d="M36,44 Q31,58 35,80 Q38,94 44,102 Q36,88 35,66 Q35,52 36,44" fill="url(#hair)"/>
        <path d="M124,44 Q129,58 125,80 Q122,94 116,102 Q124,88 125,66 Q125,52 124,44" fill="url(#hair)"/>
        <!-- 发饰（小花） -->
        <circle cx="110" cy="27" r="5" fill="#ff8fab"/><circle cx="115" cy="32" r="5" fill="#ffb3c6"/><circle cx="105" cy="32" r="5" fill="#ffb3c6"/><circle cx="110" cy="32" r="2.8" fill="#fff0f3"/>
        <!-- 腮红 -->
        <ellipse cx="50" cy="74" rx="12" ry="7" fill="url(#cheek)"/>
        <ellipse cx="110" cy="74" rx="12" ry="7" fill="url(#cheek)"/>
        <!-- 眼睛（带睫毛，眨眼动画） -->
        <g class="av-eye"><ellipse cx="60" cy="57" rx="6.5" ry="8" fill="#4a3126"/><circle cx="62.5" cy="54" r="2.2" fill="#fff"/><path d="M52,50 Q60,45.5 68,50" fill="none" stroke="#4a3126" stroke-width="1.4" stroke-linecap="round"/></g>
        <g class="av-eye r"><ellipse cx="100" cy="57" rx="6.5" ry="8" fill="#4a3126"/><circle cx="102.5" cy="54" r="2.2" fill="#fff"/><path d="M92,50 Q100,45.5 108,50" fill="none" stroke="#4a3126" stroke-width="1.4" stroke-linecap="round"/></g>
        <!-- 眉毛 -->
        <path d="M46,42.5 Q60,36.5 70,42.5" fill="none" stroke="#8a5a44" stroke-width="2.2" stroke-linecap="round"/>
        <path d="M90,42.5 Q100,36.5 114,42.5" fill="none" stroke="#8a5a44" stroke-width="2.2" stroke-linecap="round"/>
        <!-- 嘴巴 -->
        <path d="M68,84 Q80,91 92,84" fill="none" stroke="#d66b7d" stroke-width="2.2" stroke-linecap="round"/>
        <!-- 高光 -->
        <ellipse cx="60" cy="22" rx="16" ry="6" fill="#fff" opacity=".12"/>
    </svg>'''


def _stat_card(value: int, label: str) -> None:
    st.markdown(
        f'<div class="stat-card"><div class="stat-num">{value}</div>'
        f'<div class="stat-label">{label}</div></div>',
        unsafe_allow_html=True,
    )


def render_sidebar(on_browse=None) -> None:
    """侧边栏：题库统计（含"浏览题库"入口）、面试复盘（简洁卡片）+ 常用提示。

    on_browse：可选的"浏览题库"回调（通常传入 web.py 的题库对话框）。
    """
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
        if on_browse:
            st.button("📚 浏览题库", use_container_width=True, on_click=on_browse)
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

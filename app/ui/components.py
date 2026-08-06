"""共享 UI 组件：虚拟人物、侧边栏统计、题库浏览（单一实现，供 web.py 使用）。"""

import streamlit as st

import app.db as db
from app.agent.coach import InterviewSession


def render_avatar() -> None:
    """虚拟人物（写实插图风 SVG，带眨眼/呼吸/说话动画）。"""
    st.markdown(
        """<div class="av-center">
    <div class="av-fig">
        <svg width="200" height="270" viewBox="0 0 160 216" xmlns="http://www.w3.org/2000/svg">
            <defs>
                <radialGradient id="face" cx="48%" cy="35%"><stop offset="0%" stop-color="#ffead4"/><stop offset="55%" stop-color="#f5cfaa"/><stop offset="100%" stop-color="#d4a574"/></radialGradient>
                <radialGradient id="cheek" cx="50%" cy="50%"><stop offset="0%" stop-color="#f0a0a0" stop-opacity=".5"/><stop offset="100%" stop-color="#f0a0a0" stop-opacity="0"/></radialGradient>
                <linearGradient id="suit" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#3a4f63"/><stop offset="100%" stop-color="#1e2c3a"/></linearGradient>
                <linearGradient id="tie" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#e74c3c"/><stop offset="100%" stop-color="#a93226"/></linearGradient>
                <linearGradient id="hair" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#2c3e50"/><stop offset="100%" stop-color="#1a252f"/></linearGradient>
            </defs>
            <!-- 身体西装 --><rect x="26" y="105" width="108" height="80" rx="14" fill="url(#suit)"/>
            <!-- 衬衫 --><path d="M50,104 L80,130 L110,104" fill="#f8f9fa" stroke="url(#suit)" stroke-width="1"/>
            <!-- 领带 --><polygon points="80,106 66,138 80,162 94,138" fill="url(#tie)"/>
            <!-- 左臂 --><rect x="10" y="118" width="20" height="50" rx="10" fill="url(#suit)"/>
            <!-- 右臂 --><rect x="130" y="118" width="20" height="50" rx="10" fill="url(#suit)"/>
            <!-- 左手 --><ellipse cx="20" cy="170" rx="11" ry="13" fill="#f5cfaa"/>
            <!-- 右手 --><ellipse cx="140" cy="170" rx="11" ry="13" fill="#f5cfaa"/>
            <!-- 脖子 --><rect x="66" y="95" width="28" height="16" rx="6" fill="#eac09e"/>
            <!-- 头部 --><ellipse cx="80" cy="60" rx="48" ry="56" fill="url(#face)"/>
            <!-- 耳朵 --><ellipse cx="30" cy="62" rx="8" ry="14" fill="#eac09e"/><ellipse cx="130" cy="62" rx="8" ry="14" fill="#eac09e"/>
            <!-- 头发 --><path d="M32,45 Q32,0 80,-4 Q128,0 128,45 Q128,28 80,24 Q32,28 32,45" fill="url(#hair)"/>
            <path d="M34,30 Q56,16 80,14 Q104,16 126,30" fill="#3d566e" opacity=".6"/>
            <!-- 腮红 --><ellipse cx="48" cy="75" rx="14" ry="9" fill="url(#cheek)"/><ellipse cx="112" cy="75" rx="14" ry="9" fill="url(#cheek)"/>
            <!-- 眼镜框 --><rect x="42" y="48" width="32" height="20" rx="6" fill="#f8f9fa" stroke="#2c3e50" stroke-width="2.2"/><rect x="86" y="48" width="32" height="20" rx="6" fill="#f8f9fa" stroke="#2c3e50" stroke-width="2.2"/>
            <!-- 眼镜腿 --><line x1="34" y1="58" x2="42" y2="58" stroke="#2c3e50" stroke-width="2"/><line x1="118" y1="58" x2="126" y2="58" stroke="#2c3e50" stroke-width="2"/>
            <!-- 眼镜桥 --><line x1="74" y1="57" x2="86" y2="57" stroke="#2c3e50" stroke-width="2"/>
            <!-- 左眼 --><ellipse class="av-eye" cx="58" cy="57" rx="6" ry="7" fill="#1a1a2e"/><circle cx="60" cy="54" r="2" fill="#fff"/>
            <!-- 右眼 --><ellipse class="av-eye r" cx="102" cy="57" rx="6" ry="7" fill="#1a1a2e"/><circle cx="104" cy="54" r="2" fill="#fff"/>
            <!-- 眉毛 --><path d="M44,42 Q58,36 68,44" fill="none" stroke="#1a1a2e" stroke-width="2.5" stroke-linecap="round"/><path d="M92,44 Q102,36 116,42" fill="none" stroke="#1a1a2e" stroke-width="2.5" stroke-linecap="round"/>
            <!-- 闭嘴 --><path class="mc" d="M66,85 Q80,92 94,85" fill="none" stroke="#1a1a2e" stroke-width="2.3" stroke-linecap="round"/>
            <!-- 张嘴 --><ellipse class="mo" cx="80" cy="85" rx="8" ry="5" fill="#1a1a2e"/>
            <!-- 高光 --><ellipse cx="60" cy="30" rx="18" ry="8" fill="#fff" opacity=".1"/>
        </svg>
    </div>
    <div class="av-name">面试官小P</div>
    <div class="av-st"><i></i>在线</div>
</div>""",
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    """侧边栏：题库统计与数据源说明。"""
    db.init_db()  # 幂等：确保表存在（禁用调度器时也安全）
    with st.sidebar:
        st.title("🎤 面试官小P")
        st.caption("题库实时爬取 · 定时自动更新")
        st.divider()
        st.subheader("📚 题库统计")
        st.write(f"总题数：**{db.count_questions()}**")
        for r in db.count_by_source():
            st.write(f"- {r['source']}: {r['n']}")
        st.divider()
        st.caption("数据源：面试鸭 / LeetCode\n（牛客等更多源待接入）")
        st.divider()
        st.caption(
            "📞 右下角按钮打开独立语音通话页\n（新标签页，像打电话一样；需先启动 voice_server）"
        )


def render_question_bank() -> None:
    """题库浏览：筛选 + 「出这道题」直接进入模拟面试。"""
    db.init_db()
    with st.expander("📚 题库浏览（点「出这道题」直接进入面试）"):
        srcs = ["全部"] + [r["source"] for r in db.count_by_source()]
        c1, c2, c3 = st.columns(3)
        with c1:
            src_filter = st.selectbox("来源", srcs)
        with c2:
            diff_filter = st.selectbox("难度", ["全部", "简单", "中等", "困难"])
        with c3:
            kw = st.text_input("关键词", placeholder="如 Redis / 索引")
        q_rows = db.search_questions(
            source=None if src_filter == "全部" else src_filter,
            difficulty=None if diff_filter == "全部" else diff_filter,
            keyword=kw or None,
            limit=30,
        )
        if not q_rows:
            st.caption("暂无匹配的题目")
        for r in q_rows:
            st.markdown(f"**{r['title']}**")
            meta = f"`{r['source']}` · `{r['difficulty'] or '难度未知'}`"
            c = st.columns([4, 1])
            c[0].caption(meta + (f" · {r['url']}" if r["url"] else ""))
            if c[1].button("出这道题", key=f"ask_{r['id']}"):
                sess = st.session_state.session
                if sess is None:
                    sess = InterviewSession("mock")
                    st.session_state.session = sess
                with st.spinner("小P出题中…"):
                    try:
                        reply = sess.ask_question_by_id(r["id"])
                    except Exception as e:
                        reply = None
                        st.error(f"出题失败：{e}")
                if reply:
                    st.session_state.history.append(("assistant", reply))
                    # 不能在此处直接改 st.session_state.mode（radio 已实例化），
                    # 用标记在下一轮渲染 radio 之前应用
                    st.session_state["_force_mode"] = "模拟面试"
            st.divider()

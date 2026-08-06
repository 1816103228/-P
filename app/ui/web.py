"""面试官小P - Streamlit 聊天界面（主入口，单一实现）。

包含：模拟面试 / 辅导答疑双模式、定制面试、题库浏览、
语音交互（语音输入 + 回答播报）与虚拟人物动画。

启动：
    streamlit run app/ui/web.py
或双击 scripts/start.bat（Windows）/ 运行 scripts/start.sh（macOS / Linux）。
"""
import html
import logging
import os
import threading

import streamlit as st

from app import config
import app.db as db
from app.agent.coach import InterviewSession, generate_interview_questions
from app.agent.llm import is_api_key_configured
from app.prompts import MOCK_GREETING
from app.scheduler import setup_logging, start_scheduler
from app.ui.components import avatar_svg, render_sidebar

st.set_page_config(page_title="面试官小P", page_icon="🎤", layout="centered")

_log = logging.getLogger("ui")

# ---- 定时爬取 ----
_scheduler_started = False
_scheduler_lock = threading.Lock()


def _ensure_scheduler():
    global _scheduler_started
    if os.getenv("DISABLE_SCHEDULER") == "1":
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        setup_logging()
        start_scheduler()
        _scheduler_started = True


_ensure_scheduler()

# ---- 密钥检查 ----
if not is_api_key_configured():
    st.warning(
        "⚠️ 未检测到 **DeepSeek API Key**：请编辑 `.env` 填入密钥"
        "（[platform.deepseek.com](https://platform.deepseek.com) 获取）后重启服务。"
    )

# ---- CSS ----
st.markdown(
    """<style>
/* ===== 全局 ===== */
[data-testid="stAppViewContainer"], .stApp {
    background: radial-gradient(1100px 560px at 50% -10%, #e9f0ff 0%, #f8fafd 50%, #f2f5fa 100%);
}
.block-container { padding: 1rem 1.1rem 7rem !important; max-width: 780px !important; }
html, body, .stApp, .stMarkdown, .stButton, .stTextInput, .stTextArea, .stSelectbox {
    font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, -apple-system, sans-serif !important;
}
#MainMenu, footer { visibility: hidden; display: none; }
[data-testid="stToolbar"] { display: none; }
[data-testid="stSidebar"] { background: rgba(255,255,255,.80); border-right: 1px solid #e7ecf4; }
[data-testid="stSidebar"] .block-container { padding-top: 1.1rem; }
[data-testid="stSidebarContent"]::-webkit-scrollbar { width: 0; }

/* ===== 品牌头部 ===== */
.brand { display: flex; align-items: center; gap: 12px; }
.brand-avatar { width: 58px; height: 76px; display: flex; align-items: center; justify-content: center;
    background: linear-gradient(160deg, #ffffff, #edf3fc); border: 1px solid #e2e9f4;
    border-radius: 16px; box-shadow: 0 4px 14px rgba(31,58,102,.10); }
.brand-name { font-size: 19px; font-weight: 700; color: #1e2c46; letter-spacing: .4px; }
.brand-status { margin-top: 3px; font-size: 12px; color: #3f8f63; display: flex; align-items: center; gap: 6px; }
.brand-status i { width: 7px; height: 7px; border-radius: 50%; background: #22c55e;
    box-shadow: 0 0 0 3px rgba(34,197,94,.14); animation: dp 2.2s ease-in-out infinite; }
@keyframes dp { 0%,100%{opacity:1} 50%{opacity:.35} }

/* 眨眼 */
.av-eye { animation: bl 4.5s ease-in-out infinite; }
.av-eye.r { animation-delay: .18s; }
@keyframes bl { 0%,96%,100%{transform:scaleY(1)} 98%{transform:scaleY(.08)} }

/* ===== 模式胶囊 ===== */
button[data-variant="pills"] { border-radius: 999px !important; font-weight: 600; font-size: 13px;
    padding: 4px 14px; border: 1px solid #dfe6f2 !important; background: #fff !important;
    color: #3c4a63 !important; box-shadow: none !important; transition: all .15s; }
button[data-variant="pills"]:hover { border-color: #9db2e8 !important; color: #4f6ef7 !important; }
button[data-variant="pills"][aria-checked="true"] { background: #4f6ef7 !important; color: #fff !important;
    border-color: #4f6ef7 !important; box-shadow: 0 2px 8px rgba(79,110,247,.30) !important; }

/* ===== 按钮 ===== */
.stButton button { border-radius: 10px; font-weight: 600; font-size: 14px; border: 1px solid #dfe6f1;
    color: #33415c; background: #fff; box-shadow: 0 1px 3px rgba(20,40,80,.04); transition: all .15s; }
.stButton button:hover { border-color: #9db2e8; color: #4f6ef7; }
.stButton button[kind="primary"] { background: linear-gradient(135deg, #4f6ef7, #6a86f9); color: #fff;
    border: none; box-shadow: 0 3px 10px rgba(79,110,247,.28); }
.stButton button[kind="primary"]:hover { background: linear-gradient(135deg, #4160e8, #5b79f7); color: #fff; }

/* ===== 聊天 ===== */
.msg.user { display: flex; justify-content: flex-end; margin: 14px 0; }
.msg.user .bubble { background: linear-gradient(135deg, #4f6ef7, #6482f8); color: #fff; padding: 10px 15px;
    border-radius: 16px 16px 5px 16px; font-size: 15px; line-height: 1.7;
    box-shadow: 0 3px 10px rgba(79,110,247,.22); word-break: break-word; max-width: 82%; }
[data-testid="stChatMessage"] { background: rgba(255,255,255,.92); border: 1px solid #e8edf6;
    border-radius: 16px 16px 16px 5px; padding: 12px 16px; margin: 14px 0;
    box-shadow: 0 1px 4px rgba(20,40,80,.05); }
[data-testid="stChatMessage"] p { margin: 0; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] { font-size: 15px; line-height: 1.7; }

/* ===== 欢迎区 ===== */
.hero { text-align: center; margin: 34px 0 22px; }
.hero-title { font-size: 23px; font-weight: 700; color: #1e2c46; letter-spacing: .5px; }
.hero-sub { margin-top: 8px; font-size: 13px; color: #7b879c; letter-spacing: .5px; }
.qcard { background: #fff; border: 1px solid #e7edf6; border-radius: 16px; padding: 20px 12px 14px;
    text-align: center; box-shadow: 0 2px 10px rgba(20,40,80,.05); margin-bottom: 10px; }
.qicon { font-size: 30px; line-height: 1; }
.qtitle { margin-top: 10px; font-size: 15px; font-weight: 700; color: #243352; }
.qdesc { margin-top: 5px; font-size: 12px; color: #8792a7; }

/* ===== 侧边栏 ===== */
.side-title { font-size: 13px; font-weight: 700; color: #56637a; letter-spacing: 1px; margin-bottom: 10px; }
.stat-card { background: #fff; border: 1px solid #e7edf6; border-radius: 12px; padding: 10px 4px;
    text-align: center; box-shadow: 0 1px 4px rgba(20,40,80,.04); }
.stat-num { font-size: 19px; font-weight: 700; color: #243352; }
.stat-label { margin-top: 2px; font-size: 11px; color: #8a95aa; }
.side-note { font-size: 12px; color: #7b879c; margin: 12px 0 0; }
.rev-row { display: flex; align-items: center; justify-content: space-between;
    padding: 6px 8px; margin: 4px 0; background: #fff; border: 1px solid #eef1f7;
    border-radius: 10px; font-size: 12px; color: #56637a; }
.rev-score { font-weight: 700; color: #4f6ef7; }
.rev-meta { color: #8a95aa; }

/* ===== 输入框 ===== */
[data-testid="stChatInput"] { border: 1px solid #e2e8f2; border-radius: 14px; background: #fff;
    box-shadow: 0 4px 16px rgba(20,40,80,.08); }
[data-testid="stChatInput"] textarea { border-radius: 14px !important; }

/* ===== 语音通话 ===== */
#vccall { position: fixed; bottom: 28px; right: 28px; z-index: 9999; width: 64px; height: 64px;
    border-radius: 50%; background: #4f6ef7; border: none; cursor: pointer;
    box-shadow: 0 4px 18px rgba(79,110,247,.35); display: flex; align-items: center;
    justify-content: center; font-size: 26px; transition: all .25s; color: #fff; }
#vccall:hover { transform: scale(1.08); box-shadow: 0 6px 24px rgba(79,110,247,.5); }
#vccall.on { background: #e74c3c; animation: micPulse .9s ease-in-out infinite; }
@keyframes micPulse { 0%,100%{box-shadow:0 0 0 0 rgba(231,76,60,.5)} 50%{box-shadow:0 0 0 16px rgba(231,76,60,0)} }
#vcstatus { position: fixed; bottom: 104px; right: 28px; z-index: 9999; background: #fff; border-radius: 10px;
    padding: 6px 14px; font-size: 13px; color: #333; box-shadow: 0 2px 10px rgba(0,0,0,.12);
    display: none; max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>""",
    unsafe_allow_html=True,
)

# ---- 会话状态 ----
if "session" not in st.session_state:
    st.session_state.session = None
if "history" not in st.session_state:
    st.session_state.history = []
if "mode" not in st.session_state:
    st.session_state.mode = "模拟面试"

# 出题/定制面试后的模式同步（必须在 pills 实例化之前应用）
if "_force_mode" in st.session_state:
    st.session_state.mode = st.session_state.pop("_force_mode")


def start_mock():
    st.session_state.session = InterviewSession("mock")
    st.session_state.history = [("assistant", MOCK_GREETING)]
    st.session_state.mode = "模拟面试"


def switch_coach():
    st.session_state.mode = "辅导答疑"


def clear_all():
    st.session_state.session = None
    st.session_state.history = []


# ---- 对话框：定制面试 ----
@st.dialog("🎯 定制面试")
def custom_interview_dialog():
    job_title = st.text_input("目标岗位", placeholder="如：Python 后端工程师")
    jd = st.text_area("招聘信息（选填）", placeholder="粘贴岗位职责与任职要求…", height=120)
    if st.button("生成面试题", type="primary", use_container_width=True):
        if not job_title.strip() and not jd.strip():
            st.warning("请填写目标岗位或招聘信息")
            return
        with st.spinner("正在生成定制面试题…"):
            try:
                qs = generate_interview_questions(job_title, jd)
            except Exception as e:
                st.error(f"生成失败：{e}")
                qs = []
        if qs:
            st.session_state.session = InterviewSession(
                "mock", questions=qs, job_title=job_title, jd=jd
            )
            st.session_state.history = [
                (
                    "assistant",
                    f"已按「{job_title.strip() or '自定义'}」为你生成 **{len(qs)} 道定制题**。\n\n"
                    "先做个 1 分钟自我介绍吧（姓名 / 经验 / 相关项目）😊",
                )
            ]
            st.session_state["_force_mode"] = "模拟面试"
            st.rerun()


# ---- 对话框：题库浏览 ----
@st.dialog("📚 题库")
def question_bank_dialog():
    srcs = ["全部"] + [r["source"] for r in db.count_by_source()]
    c1, c2, c3 = st.columns(3)
    with c1:
        src_filter = st.selectbox("来源", srcs, key="qb_src")
    with c2:
        diff_filter = st.selectbox("难度", ["全部", "简单", "中等", "困难"], key="qb_diff")
    with c3:
        kw = st.text_input("关键词", placeholder="如 Redis / 索引", key="qb_kw")
    q_rows = db.search_questions(
        source=None if src_filter == "全部" else src_filter,
        difficulty=None if diff_filter == "全部" else diff_filter,
        keyword=kw or None,
        limit=30,
    )
    if not q_rows:
        st.caption("暂无匹配的题目")
    for r in q_rows:
        c = st.columns([4, 1], vertical_alignment="center")
        c[0].markdown(
            f"**{r['title']}**\n\n`{r['source']}` · `{r['difficulty'] or '难度未知'}`"
        )
        if c[1].button("出这道题", key=f"qb_ask_{r['id']}"):
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
                st.session_state["_force_mode"] = "模拟面试"
                st.rerun()


# ---- 顶部：品牌 + 模式 + 操作 ----
h1, h2 = st.columns([1, 1.15], vertical_alignment="center")
with h1:
    st.markdown(
        f'<div class="brand"><div class="brand-avatar">{avatar_svg(46)}</div>'
        '<div><div class="brand-name">面试官小P</div>'
        '<div class="brand-status"><i></i>在线</div></div></div>',
        unsafe_allow_html=True,
    )
with h2:
    st.pills("模式", ["模拟面试", "辅导答疑"], key="mode", label_visibility="collapsed")

a1, a2, a3, a4 = st.columns(4)
with a1:
    st.button("开始面试", use_container_width=True, type="primary", on_click=start_mock)
with a2:
    if st.button("定制面试", use_container_width=True):
        custom_interview_dialog()
with a3:
    if st.button("浏览题库", use_container_width=True):
        question_bank_dialog()
with a4:
    st.button("清空", use_container_width=True, on_click=clear_all)

render_sidebar()


# ---- 欢迎区 / 聊天 ----
def render_welcome():
    st.markdown(
        '<div class="hero"><div class="hero-title">开始你的面试训练</div>'
        '<div class="hero-sub">模拟面试 · 辅导答疑 · 定制练习</div></div>',
        unsafe_allow_html=True,
    )
    cards = [
        ("🎤", "模拟面试", "难度递进 · 逐题点评", "开始面试", start_mock),
        ("💡", "辅导答疑", "标准回答 · 加分点 · 变式题", "开始答疑", switch_coach),
        ("🎯", "定制面试", "按岗位与 JD 生成专属题", "开始定制", lambda: custom_interview_dialog()),
    ]
    cols = st.columns(3)
    for col, (icon, title, desc, label, action) in zip(cols, cards):
        with col:
            st.markdown(
                f'<div class="qcard"><div class="qicon">{icon}</div>'
                f'<div class="qtitle">{title}</div><div class="qdesc">{desc}</div></div>',
                unsafe_allow_html=True,
            )
            if st.button(label, key=f"card_{title}", use_container_width=True):
                action()


if not st.session_state.history:
    render_welcome()
else:
    for role, text in st.session_state.history:
        if role == "user":
            body = html.escape(text).replace("\n", "<br>")
            st.markdown(
                f'<div class="msg user"><div class="bubble">{body}</div></div>',
                unsafe_allow_html=True,
            )
        else:
            with st.chat_message("assistant", avatar="🎤"):
                st.markdown(text)

# ---- 输入 ----
prompt = st.chat_input("输入你的回答或问题…")
if prompt:
    sess = st.session_state.session
    if sess is None:
        # 仅在无会话时按当前模式新建；已有会话（如「出这道题」的模拟面试）则继续沿用
        _mode = "mock" if st.session_state.mode.startswith("模拟") else "coach"
        sess = InterviewSession(_mode)
        st.session_state.session = sess

    st.session_state.history.append(("user", prompt))
    body = html.escape(prompt).replace("\n", "<br>")
    st.markdown(
        f'<div class="msg user"><div class="bubble">{body}</div></div>',
        unsafe_allow_html=True,
    )

    try:
        with st.chat_message("assistant", avatar="🎤"):
            reply = st.write_stream(sess.handle_stream(prompt))
        reply = (reply or "").strip()
    except Exception:
        _log.exception("对话异常")
        reply = "小P暂时无法回答，请稍后重试。"
        with st.chat_message("assistant", avatar="🎤"):
            st.error(reply)
    st.session_state.history.append(("assistant", reply or "小P暂时无法回答，请稍后重试。"))

# ---- 语音通话（独立语音通话页，像打电话一样）----
# 右下角按钮在新标签页打开 FastAPI 托管的语音通话页（http://{host}:{port}/），
# 不受 Streamlit rerun / iframe 限制，通话过程与文字版完全隔离。
voice_url = f"http://{config.VOICE_HOST}:{config.VOICE_PORT}/"
st.markdown(
    f'<a id="vccall" href="{voice_url}" target="_blank" title="打开独立语音通话页">📞</a>',
    unsafe_allow_html=True,
)

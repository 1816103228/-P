"""面试官小P - Streamlit 聊天界面（主入口，单一实现）。

包含：模拟面试 / 辅导答疑双模式、题库浏览、侧边栏统计、
语音交互（语音输入 + 回答播报）与虚拟人物动画。

启动：
    streamlit run main.py
或双击项目根目录的 启动.bat。
"""
import logging
import os
import threading
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from app import config
from app.agent.coach import InterviewSession, generate_interview_questions
from app.agent.llm import is_api_key_configured
from app.prompts import MOCK_GREETING
from app.scheduler import setup_logging, start_scheduler
from app.ui.components import render_avatar, render_question_bank, render_sidebar

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

# ---- 密钥检查：缺 Key 时给出明确提示，而不是点击功能时才报"出错了" ----
if not is_api_key_configured():
    st.warning(
        "⚠️ 未检测到有效的 **DeepSeek API Key**：请编辑项目根目录的 `.env` 文件，"
        "填入真实密钥（[platform.deepseek.com](https://platform.deepseek.com) 获取）并重启服务，"
        "否则对话、语音、出题等功能不可用。"
    )

# ---- CSS ----
st.markdown(
    """<style>
.block-container { padding:0!important; max-width:600px!important; }
/* 人物居中 */
.av-center { display:flex; flex-direction:column; align-items:center; padding:18px 0 8px 0; }
.av-fig { animation: br 4.5s ease-in-out infinite; }
.av-fig svg { display:block; }
@keyframes br { 0%,100%{transform:translateY(0) rotate(0deg)} 25%{transform:translateY(-5px) rotate(-.5deg)} 75%{transform:translateY(-3px) rotate(.5deg)} }
/* 眨眼 */
.av-eye { animation: bl 4.5s ease-in-out infinite; }
.av-eye.r { animation-delay:.18s; }
@keyframes bl { 0%,96%,100%{transform:scaleY(1)} 98%{transform:scaleY(.08)} }
/* 嘴巴 */
.mc { animation: tc 3.5s ease-in-out infinite; }
.mo { opacity:0; animation: to 3.5s ease-in-out infinite; }
@keyframes tc { 0%,43%,100%{opacity:1} 51%{opacity:0} }
@keyframes to { 0%,43%,100%{opacity:0} 51%{opacity:1} }
.av-name { margin-top:6px; font-size:16px; font-weight:600; color:#1a1a2e; letter-spacing:1px; }
.av-st { font-size:12px; color:#4CAF50; }
.av-st i { display:inline-block; width:6px; height:6px; border-radius:50%; background:#4CAF50; margin-right:5px; animation:dp 2s ease-in-out infinite; }
@keyframes dp { 0%,100%{opacity:1} 50%{opacity:.3} }
/* 顶栏 */
.top { display:flex; justify-content:center; gap:8px; margin:4px 0; flex-wrap:wrap; }
.stChatMessage { border-radius:14px!important; }
[data-testid="stChatInput"] textarea { border-radius:12px!important; }
/* 语音通话按钮 */
#vccall { position:fixed; bottom:28px; right:28px; z-index:9999; width:64px; height:64px; border-radius:50%; background:#4f6ef7; border:none; cursor:pointer; box-shadow:0 4px 18px rgba(79,110,247,.35); display:flex; align-items:center; justify-content:center; font-size:26px; transition:all .25s; color:#fff; }
#vccall:hover { transform:scale(1.08); box-shadow:0 6px 24px rgba(79,110,247,.5); }
#vccall.on { background:#e74c3c; animation:micPulse .9s ease-in-out infinite; }
@keyframes micPulse { 0%,100%{box-shadow:0 0 0 0 rgba(231,76,60,.5)} 50%{box-shadow:0 0 0 16px rgba(231,76,60,0)} }
#vcstatus { position:fixed; bottom:104px; right:28px; z-index:9999; background:#fff; border-radius:10px; padding:6px 14px; font-size:13px; color:#333; box-shadow:0 2px 10px rgba(0,0,0,.12); display:none; max-width:300px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
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

# 出题/定制面试后的模式同步（必须在 radio 实例化之前应用）
if "_force_mode" in st.session_state:
    st.session_state.mode = st.session_state.pop("_force_mode")


def start_mock():
    st.session_state.session = InterviewSession("mock")
    st.session_state.history = [("assistant", MOCK_GREETING)]
    st.session_state.mode = "模拟面试"


def clear_all():
    st.session_state.session = None
    st.session_state.history = []


# ---- 顶栏 ----
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    st.radio("模式", ["模拟面试", "辅导答疑"], index=0, horizontal=True, label_visibility="collapsed", key="mode")
with c2:
    st.button("开始面试", use_container_width=True, type="primary", on_click=start_mock)
with c3:
    st.button("清空", use_container_width=True, on_click=clear_all)

render_avatar()
render_sidebar()
render_question_bank()

# ---- 定制面试：目标岗位 + 招聘信息，生成专属面试题 ----
with st.expander("🎯 定制面试：输入目标岗位与招聘信息，生成专属面试题"):
    job_title = st.text_input("目标岗位", placeholder="如：高级 Python 后端工程师")
    jd = st.text_area("招聘信息 / JD", placeholder="粘贴岗位职责与任职要求…", height=130)
    if st.button("生成定制面试题并开始", use_container_width=True):
        if not job_title.strip() and not jd.strip():
            st.warning("请至少输入目标岗位或招聘信息")
        else:
            with st.spinner("小P 正在根据招聘信息生成定制面试题…"):
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
                        f"你好！已根据目标岗位「{job_title.strip() or '自定义'}」和招聘信息为你生成 "
                        f"**{len(qs)} 道定制面试题**，难度由浅入深。\n\n"
                        "先做个 1 分钟自我介绍吧（姓名、经验、与岗位相关的项目）😊",
                    )
                ]
                st.session_state["_force_mode"] = "模拟面试"
                st.success(f"已生成 {len(qs)} 道定制题，开始作答吧")

# ---- 聊天 ----
for role, text in st.session_state.history:
    avatar = "🎤" if role == "assistant" else None
    with st.chat_message(role, avatar=avatar):
        st.markdown(text)

if not st.session_state.history:
    st.info("点击 **开始面试** 进入模拟面试，或直接输入问题进入 **辅导答疑**")

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
    with st.chat_message("user"):
        st.markdown(prompt)

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

# ---- 语音通话（像打电话一样：边说边答、随时打断）----
# 可见元素：通话按钮 + 状态条
st.markdown(
    '<div id="vccall">📞</div><div id="vcstatus">未连接</div>',
    unsafe_allow_html=True,
)
# JS 通过 iframe 注入并操作父窗口 DOM；状态挂在 parent.__voiceState，
# 这样 Streamlit 重跑时只重新绑定按钮，不会断开通话。
voice_js = (Path(__file__).resolve().parent / "app" / "ui" / "voice_script.js").read_text(encoding="utf-8")
components.html(
    voice_js.replace("__VOICE_PORT__", str(config.VOICE_PORT))
            .replace("__VAD_THRESHOLD__", str(config.VOICE_VAD_THRESHOLD)),
    height=0,
)

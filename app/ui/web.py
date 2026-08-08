"""面试官小P - Streamlit 聊天界面（主入口，单一实现）。

包含：模拟面试 / 辅导答疑双模式、定制面试、题库浏览、
语音交互（语音输入 + 回答播报）与虚拟人物动画。

启动：
    streamlit run app/ui/web.py
或双击 scripts/start.bat（Windows）/ 运行 scripts/start.sh（macOS / Linux）。
"""
import base64
import html
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

import streamlit as st

import app.db as db
import app.importer as importer
import app.voice_store as voice_store
from app import config
from app.agent.coach import InterviewSession
from app.agent.customizer import generate_interview_questions
from app.agent.llm import is_api_key_configured
from app.prompts import MOCK_GREETING, PERSONAS
from app.scheduler import setup_logging, start_scheduler
from app.ui.components import avatar_svg, render_sidebar

#: 题库来源的显示名（数据库存英文标识，界面统一展示中文）
SOURCE_LABELS = {
    "mianshiya": "面试鸭",
    "leetcode": "LeetCode",
    "nowcoder": "牛客",
    "custom": "自定义",
}


st.set_page_config(page_title="面试官小P", page_icon="🎤", layout="centered")

_log = logging.getLogger("ui")
voice_url = f"http://{config.VOICE_HOST}:{config.VOICE_PORT}/"


def _avatar_html() -> str:
    """品牌头部头像：优先使用 AI 生成的真人形象照（小图 base64 内嵌），缺失时回退 SVG。"""
    p = Path(__file__).resolve().parent / "assets" / "avatar_small.png"
    if not p.exists():
        return avatar_svg(46)
    stamp = (p.stat().st_mtime_ns, p.stat().st_size)
    return _avatar_data_uri(stamp, str(p))


@st.cache_data(show_spinner=False)
def _avatar_data_uri(stamp: tuple, path: str) -> str:
    b64 = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" alt="小P"/>'


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
.block-container { padding: 0.6rem 1.1rem 0.5rem !important; max-width: 780px !important; }
html, body, .stApp, .stMarkdown, .stButton, .stTextInput, .stTextArea, .stSelectbox {
    font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, -apple-system, sans-serif !important;
}
footer { visibility: hidden; display: none; }
/* 只隐藏 Deploy 与主菜单，保留工具栏（收起侧边栏后左上角的展开按钮在其中） */
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"] { display: none !important; }
/* 头部改为普通文档流，避免悬浮盖住首页顶部内容 */
[data-testid="stHeader"] { position: relative !important; background: transparent !important; z-index: 1; }
[data-testid="stSidebar"] { background: rgba(255,255,255,.80); border-right: 1px solid #e7ecf4; }
[data-testid="stSidebar"] .block-container { padding-top: 1.1rem; }
[data-testid="stSidebarContent"]::-webkit-scrollbar { width: 0; }

/* ===== 品牌头部 ===== */
.brand { display: flex; align-items: center; gap: 12px; }
.brand-center { justify-content: center; }
.brand-avatar { width: 56px; height: 56px; flex: 0 0 56px; border-radius: 50%; overflow: hidden;
    border: 2px solid #fff; box-shadow: 0 4px 14px rgba(31,58,102,.16); }
.brand-avatar img { width: 100%; height: 100%; object-fit: cover; object-position: center 28%; display: block; }
.brand-avatar svg { width: 46px; height: 46px; }
.brand-name { font-size: 19px; font-weight: 700; color: #1e2c46; letter-spacing: .4px; }
.brand-status { margin-top: 3px; font-size: 12px; color: #3f8f63; display: flex; align-items: center; gap: 6px; }
.brand-status i { width: 7px; height: 7px; border-radius: 50%; background: #22c55e;
    box-shadow: 0 0 0 3px rgba(34,197,94,.14); animation: dp 2.2s ease-in-out infinite; }
@keyframes dp { 0%,100%{opacity:1} 50%{opacity:.35} }
.chat-mode { font-size: 13px; color: #7b879c; letter-spacing: .4px; }

/* 眨眼 */
.av-eye { animation: bl 4.5s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
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
    border-radius: 16px 16px 16px 5px; padding: 14px 18px 20px; margin: 14px 0;
    box-shadow: 0 1px 4px rgba(20,40,80,.05); max-width: 96%; }
[data-testid="stChatMessage"] p { margin: 0 0 .4em; }
[data-testid="stChatMessage"] p:last-child { margin-bottom: 0; }
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] { font-size: 15px; line-height: 1.7; }

/* ===== 消息白框纵向延伸：底部余量一直留到输入框 ===== */
[data-testid="stMainBlockContainer"] { flex: 1; display: flex; flex-direction: column; }
[data-testid="stMainBlockContainer"] > [data-testid="stVerticalBlock"] { flex: 1; display: flex; flex-direction: column; }
[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(+ [data-testid="stElementContainer"]:last-child) { flex: 1 1 auto; }
[data-testid="stLayoutWrapper"] > [data-testid="stChatMessage"] { flex: 1; height: auto; }
[data-testid="stVerticalBlock"] > [data-testid="stLayoutWrapper"]:has(+ [data-testid="stElementContainer"]:last-child) > [data-testid="stChatMessage"] { margin-bottom: 0; }
[data-testid="stChatMessageContent"] { margin: 0 !important; align-self: flex-start !important; }
/* 压缩 Streamlit 输入框上方的固定占位，让白框能延伸到输入框上方 */
[data-testid="stAppScrollToBottomContainer"] > div:not([data-testid="stMainBlockContainer"]):not([data-testid="stBottom"]) {
    flex: 0 0 0 !important;
    height: 0 !important;
}

/* ===== 欢迎区 ===== */
.hero { text-align: center; margin: 12px 0 10px; }
.hero-title { font-size: 21px; font-weight: 700; color: #1e2c46; letter-spacing: .5px; }
.hero-sub { margin-top: 6px; font-size: 13px; color: #7b879c; letter-spacing: .5px; }
.qcard { background: #fff; border: 1px solid #e7edf6; border-radius: 16px; padding: 12px 10px 8px;
    text-align: center; box-shadow: 0 2px 10px rgba(20,40,80,.05); margin-bottom: 6px; }
.qicon { font-size: 26px; line-height: 1; }
.qtitle { margin-top: 6px; font-size: 15px; font-weight: 700; color: #243352; }
.qdesc { margin-top: 4px; font-size: 12px; color: #8792a7; }

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
.voice-ready { background: linear-gradient(135deg, #eef3ff, #e6edff); border: 1px solid #cdd9f7;
    border-radius: 12px; padding: 10px 14px; font-size: 13px; color: #33508c; }

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
/* 选中的标签药丸高亮：填色 + 白字 + 外圈光晕，一眼可见 */
button[data-variant="pills"][data-selected="true"] {
    background: #4f6ef7 !important;
    border-color: #4f6ef7 !important;
    color: #fff !important;
    box-shadow: 0 0 0 2px rgba(79,110,247,.35) !important;
    font-weight: 600 !important;
}
button[data-variant="pills"]:hover {
    border-color: #4f6ef7 !important;
}
/* 题库对话框加宽：左侧题目列表 + 右侧"已选题目"面板 */
[data-testid="stDialog"] {
    width: min(2080px, 100vw) !important;
    max-width: none !important;
}
/* 弹窗垂直居中：父容器是 flex，上下 margin:auto 撑开剩余空间；
   弹窗高度超过视口时 margin 自动归零、顶部对齐并随容器滚动，避免遮挡 */
[data-testid="stDialog"] {
    margin-top: auto !important;
    margin-bottom: auto !important;
}
/* 内容容器默认固定 500px，覆盖为铺满对话框（留 16px 边距） */
[data-testid="stDialog"] > div {
    width: 100% !important;
    max-width: calc(100% - 32px) !important;
}
/* 对话框内按钮文字不换行（"加入面试"等保持单行平铺） */
[data-testid="stDialog"] button {
    white-space: nowrap !important;
}
/* 定制面试对话框瘦身：普通表单宽度，不随题库对话框加宽 */
[data-testid="stDialog"]:has(.dialog-custom) { width: 100vw !important; }
[data-testid="stDialog"]:has(.dialog-custom) > div {
    width: min(560px, calc(100vw - 32px)) !important;
    max-width: min(560px, calc(100vw - 32px)) !important;
}
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
if "selected_questions" not in st.session_state:
    st.session_state.selected_questions = []

# 出题/定制面试后的模式同步（必须在 pills 实例化之前应用）
if "_force_mode" in st.session_state:
    st.session_state.mode = st.session_state.pop("_force_mode")


def start_mock():
    st.session_state.session = InterviewSession(
        "mock", persona=st.session_state.get("persona", "")
    )
    st.session_state.history = [("assistant", MOCK_GREETING)]
    st.session_state.mode = "模拟面试"


def switch_coach():
    st.session_state.mode = "辅导答疑"
    st.session_state.session = InterviewSession("coach")
    st.session_state.history = [
        (
            "assistant",
            "已切换到 **辅导答疑** 模式：直接输入你的面试问题，"
            "我会给出标准参考回答、加分点和一道变式题。",
        )
    ]


def reset_to_home():
    """结束当前会话并返回欢迎首页，可重新选择面试 / 答疑 / 定制。"""
    st.session_state.session = None
    st.session_state.history = []
    st.session_state.selected_questions = []
    st.session_state.pop("_force_mode", None)


def _add_selected(qid: int, title: str, difficulty: str, source: str) -> None:
    """把一道题加入"已选题目"（on_click 回调，渲染前执行，界面即时刷新）。"""
    if not any(s["id"] == qid for s in st.session_state.selected_questions):
        st.session_state.selected_questions.append(
            {"id": qid, "title": title, "difficulty": difficulty, "source": source}
        )


def _remove_selected(qid: int) -> None:
    """从"已选题目"移除一道题（on_click 回调）。"""
    st.session_state.selected_questions = [
        s for s in st.session_state.selected_questions if s["id"] != qid
    ]


def _toggle_favorite(qid: int) -> None:
    """收藏/取消收藏（on_click 回调）。"""
    if db.is_favorite(qid):
        db.remove_favorite(qid)
    else:
        db.add_favorite(qid)


# ---- 对话框：定制面试 ----
@st.dialog("🎯 定制面试")
def custom_interview_dialog():
    st.markdown('<div class="dialog-custom"></div>', unsafe_allow_html=True)
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
            # 同步保存一份给语音通话：接通电话后直接以语音方式进行这份定制面试
            voice_store.save_custom_interview(job_title, jd, qs)
            st.session_state["_custom_voice_ready"] = True
            st.session_state.session = InterviewSession(
                "mock",
                questions=qs,
                job_title=job_title,
                jd=jd,
                persona=st.session_state.get("persona", ""),
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
    with st.expander("➕ 添加自定义题"):
        cq_title = st.text_input("题干", key="cq_title", placeholder="如：如何设计一个限流组件？")
        cq_answer = st.text_area("参考答案（选填）", key="cq_answer", height=80)
        cq_tags = st.text_input("标签（逗号分隔，选填）", key="cq_tags", placeholder="如：Redis,限流")
        cq_diff = st.selectbox("难度", ["简单", "中等", "困难"], key="cq_diff")
        cq_company = st.text_input("公司（选填）", key="cq_company", placeholder="如：字节跳动")
        if st.button("添加题目", type="primary", use_container_width=True):
            if not cq_title.strip():
                st.warning("请填写题干")
            else:
                db.upsert_question(
                    source="custom",
                    title=cq_title.strip(),
                    answer=cq_answer.strip() or None,
                    tags=[t.strip() for t in cq_tags.split(",") if t.strip()] or None,
                    difficulty=cq_diff,
                    company=cq_company.strip() or None,
                )
                st.success("已添加到题库")
                st.rerun()

    with st.expander("📥 批量导入自定义题（CSV）"):
        st.caption(
            "每行一题，至少包含「题干」；可选列：答案、标签（逗号分隔）、难度（简单/中等/困难）、公司。"
            "支持表头（题目,答案,标签,难度,公司）或无表头按列顺序。"
        )
        imp_csv = st.text_area(
            "粘贴 CSV 内容",
            key="imp_csv",
            height=120,
            placeholder="解释 Python 的 GIL 机制及其影响,全局解释器锁……,Python,简单,通用\nRedis 缓存穿透如何解决？,……,Redis,中等,字节跳动",
        )
        imp_file = st.file_uploader("或上传 CSV 文件", type=["csv"], key="imp_file")
        if st.button("导入", key="imp_btn", type="primary", use_container_width=True):
            text = ""
            if imp_csv.strip():
                text = imp_csv
            elif imp_file is not None:
                text = imp_file.getvalue().decode("utf-8-sig", errors="replace")
            if not text.strip():
                st.warning("请先粘贴内容或上传 CSV 文件")
            else:
                stats = importer.import_questions_csv(text)
                if stats["rows"]:
                    msg = f"导入成功 **{stats['new']}** 条"
                    if stats["skipped"]:
                        msg += f"，跳过重复 {stats['skipped']} 条"
                    st.success(msg)
                else:
                    st.error("没有可导入的题目，请检查 CSV 格式")

    src_items = [
        (SOURCE_LABELS.get(r["source"], r["source"]), r["source"])
        for r in db.count_by_source()
    ]
    srcs = ["全部"] + [label for label, _ in src_items]
    src_key_by_label = {label: key for label, key in src_items}
    companies = ["全部"] + db.list_companies()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        src_filter = st.selectbox("来源", srcs, key="qb_src")
    with c2:
        diff_filter = st.selectbox("难度", ["全部", "简单", "中等", "困难"], key="qb_diff")
    with c3:
        company_filter = st.selectbox("公司", companies, key="qb_company")
    with c4:
        kw = st.text_input("关键词", placeholder="如 Redis / 索引", key="qb_kw")
    fav_only = st.checkbox("⭐ 仅看收藏", key="qb_fav_only")
    # 用 pills 代替 multiselect：Streamlit 多选框在弹窗里下拉面板无法收回（已知问题）
    tag_filter = st.pills(
        "标签", [t for t, _ in db.list_tags()], selection_mode="multi", key="qb_tags"
    )
    if tag_filter:
        st.caption(f"✅ 已选标签：{'、'.join(tag_filter)}（再次点击可取消）")
    q_rows = db.browse_questions(
        keyword=kw or None,
        tags=tag_filter or None,
        source=None if src_filter == "全部" else src_key_by_label.get(src_filter),
        difficulty=None if diff_filter == "全部" else diff_filter,
        company=None if company_filter == "全部" else company_filter,
        favorite_only=fav_only,
        limit=30,
    )
    sel = st.session_state.selected_questions
    # 没有已选题目时右侧面板不出现，题目列表占满整行
    if sel:
        left, right = st.columns([3, 2])
    else:
        left, right = st.container(), None
    with left:
        if not q_rows:
            st.caption("暂无匹配的题目")
        for r in q_rows:
            is_sel = any(s["id"] == r["id"] for s in sel)
            c = st.columns([4, 2, 2], vertical_alignment="center")
            src_label = SOURCE_LABELS.get(r["source"], r["source"])
            meta_parts = [html.escape(src_label), html.escape(r["difficulty"] or "难度未知")]
            if r["company"]:
                meta_parts.append(html.escape(r["company"]))
            if is_sel:
                # 已加入的题目高亮：蓝色底色 + 左侧色条 + 按钮变为"已加入"
                c[0].markdown(
                    '<div style="background:#eef2ff;border-left:3px solid #4f6ef7;'
                    'border-radius:8px;padding:6px 10px;">'
                    f'<span style="font-weight:600;">{html.escape(r["title"] or "")}</span>'
                    '<div style="color:#4f6ef7;font-size:12px;margin-top:2px;">'
                    f"✓ 已加入面试 · {html.escape(SOURCE_LABELS.get(r['source'], r['source']))} · "
                    f"{html.escape(r['difficulty'] or '难度未知')}</div></div>",
                    unsafe_allow_html=True,
                )
                c[1].button(
                    "✓ 已加入",
                    key=f"qb_add_{r['id']}",
                    type="primary",
                    use_container_width=True,
                    on_click=_remove_selected,
                    args=(r["id"],),
                )
            else:
                # 标题 + 紧凑的小灰字元信息（紧贴题目，界面更整齐）
                c[0].markdown(
                    '<div style="line-height:1.45;">'
                    f'<div style="font-weight:600;">{html.escape(r["title"] or "")}</div>'
                    f'<div style="font-size:12px;color:#6b7280;margin-top:1px;">'
                    f"{' · '.join(meta_parts)}</div></div>",
                    unsafe_allow_html=True,
                )
                c[1].button(
                    "加入面试",
                    key=f"qb_add_{r['id']}",
                    use_container_width=True,
                    on_click=_add_selected,
                    args=(r["id"], r["title"], r["difficulty"] or "未知", r["source"]),
                )
            fav_label = "★ 已收藏" if db.is_favorite(r["id"]) else "☆ 收藏"
            c[2].button(
                fav_label,
                key=f"qb_fav_{r['id']}",
                use_container_width=True,
                on_click=_toggle_favorite,
                args=(r["id"],),
            )
    if right is not None:
        with right:
            with st.container(border=True):
                st.markdown(f"**🎯 已选题目（{len(sel)}）**")
                for i, s in enumerate(sel, 1):
                    rc = st.columns([4, 1], vertical_alignment="center")
                    rc[0].caption(f"{i}. {s['title'][:22]}")
                    rc[1].button(
                        "✕", key=f"qb_del_{s['id']}", on_click=_remove_selected, args=(s["id"],)
                    )
                if st.button(
                    f"🚀 开始综合面试（{len(sel)} 题）", type="primary", use_container_width=True
                ):
                    titles = [s["title"] for s in sel]
                    st.session_state.session = InterviewSession(
                        "mock", questions=titles, job_title="综合练习"
                    )
                    st.session_state.history = [
                        (
                            "assistant",
                            f"已为你挑选 **{len(titles)} 道题**进行综合面试，"
                            "接下来由浅入深逐题提问。先做个 1 分钟自我介绍吧"
                            "（姓名 / 经验 / 相关项目）😊",
                        )
                    ]
                    st.session_state["_force_mode"] = "模拟面试"
                    st.session_state.selected_questions = []
                    st.rerun()


# ---- 顶部：品牌（居中）----
st.markdown(
    f'<div class="brand brand-center"><div class="brand-avatar">{_avatar_html()}</div>'
    '<div><div class="brand-name">面试官小P</div>'
    '<div class="brand-status"><i></i>在线</div></div></div>',
    unsafe_allow_html=True,
)

st.selectbox(
    "面试官风格",
    list(PERSONAS),
    index=0,
    key="persona",
    help="一面随和 · 二面严谨 · 三面沉稳",
)

# 生成后刷新页面也保留入口：只要语音服务端还存有待执行的定制面试，就继续展示
_custom_voice_ready = st.session_state.get("_custom_voice_ready") or (
    voice_store.load_custom_interview() is not None
)
if _custom_voice_ready:
    b1, b2, b3 = st.columns([4, 2, 1], vertical_alignment="center")
    with b1:
        st.markdown(
            '<div class="voice-ready">📞 定制面试已就绪，接通电话即可开始语音面试</div>',
            unsafe_allow_html=True,
        )
    with b2:
        st.link_button("开始语音面试", voice_url, use_container_width=True)
    with b3:
        if st.button("取消", key="cancel_custom_voice", use_container_width=True):
            voice_store.clear_custom_interview()
            st.session_state["_custom_voice_ready"] = False
            st.rerun()

# 侧边栏：题库统计 + 浏览题库入口（与统计卡片整合在一起）
render_sidebar(on_browse=question_bank_dialog)

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
        ("🎯", "定制面试", "按岗位与 JD 生成专属题", "开始定制", None),
    ]
    cols = st.columns(3)
    for col, (icon, title, desc, label, action) in zip(cols, cards, strict=False):
        with col:
            st.markdown(
                f'<div class="qcard"><div class="qicon">{icon}</div>'
                f'<div class="qtitle">{title}</div><div class="qdesc">{desc}</div></div>',
                unsafe_allow_html=True,
            )
            if action:
                # on_click 回调在渲染前执行，点击稳定生效
                st.button(
                    label, key=f"card_{title}", use_container_width=True, on_click=action
                )
            elif st.button(label, key=f"card_{title}", use_container_width=True):
                custom_interview_dialog()


if not st.session_state.history:
    render_welcome()
else:
    # 面试进行中提供返回入口，避免进入聊天视图后无法回到欢迎首页
    top_c, back_c = st.columns([4, 1], vertical_alignment="center")
    with top_c:
        st.markdown(
            f'<div class="chat-mode">当前模式：{html.escape(st.session_state.mode)}</div>',
            unsafe_allow_html=True,
        )
    with back_c:
        st.button("🏠 返回首页", on_click=reset_to_home, use_container_width=True)
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

    if getattr(sess, "finished", False) and sess.mode == "mock" and "【总分】" in (reply or ""):
        st.download_button(
            "下载总结报告 (.md)",
            reply,
            file_name=f"面试报告_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
        )

# ---- 语音通话（独立语音通话页，像打电话一样）----
# 右下角按钮在新标签页打开 FastAPI 托管的语音通话页（http://{host}:{port}/），
# 不受 Streamlit rerun / iframe 限制，通话过程与文字版完全隔离。
st.markdown(
    f'<a id="vccall" href="{voice_url}" target="_blank" title="打开独立语音通话页">📞</a>',
    unsafe_allow_html=True,
)

"""会话与聊天 REST API：启动/重置会话、查询当前状态、历史记录、SSE 流式对话。

- 会话按用户持久化（session_store），刷新/换设备可恢复；
- 聊天走 `POST /api/chat`，返回 `text/event-stream`：
    事件 `delta`  -> {"type":"delta","content":...}   增量文本
    事件 `done`   -> {"type":"done","finished":bool,"mode":str,
                      "history":[[role,content],...],"report":str|None}
    事件 `error`  -> {"type":"error","message":...}
"""

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import app.prompts as prompts
import app.session_store as session_store
from app import auth
from app.agent.coach import InterviewSession

logger = logging.getLogger("interview_coach.api.session")

router = APIRouter(prefix="/api", tags=["session"])


class StartBody(BaseModel):
    mode: str = Field(default="mock", description="mock 模拟面试 / coach 辅导答疑")
    questions: list[str] = Field(default=[], description="已选题/定制题题干列表")
    job_title: str = Field(default="", description="定制面试目标岗位")
    jd: str = Field(default="", description="定制面试招聘信息")
    persona: str = Field(default="", description="面试官人格（覆盖默认）")


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=8000)


def _build_greeting(
    session: InterviewSession,
    questions: list[str],
    job_title: str,
    jd: str,
) -> str:
    """按模式生成欢迎语（与旧 Streamlit 行为一致）。"""
    if session.mode == "coach":
        return prompts.COACH_GREETING
    if not questions:
        return prompts.MOCK_GREETING
    if job_title.strip():
        return (
            f"已按「{job_title.strip()}」为你生成 **{len(questions)} 道定制题**。\n\n"
            "先做个 1 分钟自我介绍吧（姓名 / 经验 / 相关项目）😊"
        )
    return (
        f"已为你挑选 **{len(questions)} 道题**进行综合面试，接下来由浅入深逐题提问。"
        "先做个 1 分钟自我介绍吧（姓名 / 经验 / 相关项目）😊"
    )


def _new_session(body: StartBody, user_row) -> InterviewSession:
    """按 StartBody 创建新会话（未指定人格时用用户默认）。"""
    try:
        default_persona = user_row["persona"] or ""
    except (KeyError, IndexError, TypeError):
        default_persona = ""
    persona = body.persona or default_persona
    if body.mode.startswith("mock"):
        session = InterviewSession(
            "mock",
            questions=body.questions or None,
            job_title=body.job_title,
            jd=body.jd,
            persona=persona,
        )
    else:
        session = InterviewSession("coach", persona=persona)
    greeting = _build_greeting(session, body.questions, body.job_title, body.jd)
    session.display_history = [["assistant", greeting]]
    return session


@router.post("/session/start")
def start_session(body: StartBody, user_row=auth.CurrentUser) -> dict:
    """归档旧会话并启动新会话（模拟面试 / 辅导答疑 / 综合练习）。"""
    session_store.archive_current(user_row["id"])
    session = _new_session(body, user_row)
    session_store.start_session(user_row["id"], session)
    return {
        "ok": True,
        "session_id": session.session_id,
        "mode": session.mode,
        "history": session.history_for_display(),
        "finished": False,
    }


@router.get("/session")
def get_session(user_row=auth.CurrentUser) -> dict:
    """返回当前活跃会话状态（前端刷新/恢复用）。"""
    session = session_store.load_active_session(user_row["id"])
    if session is None:
        return {
            "active": False,
            "mode": "",
            "history": [],
            "finished": False,
            "report": None,
        }
    report = None
    if session.finished and session.messages and session.messages[-1].get("role") == "assistant":
        report = session.messages[-1]["content"]
    return {
        "active": True,
        "session_id": getattr(session, "session_id", None),
        "mode": session.mode,
        "history": session.history_for_display(),
        "finished": session.finished,
        "report": report,
        "persona": session.persona,
        "custom_questions": session.custom_questions,
        "job_title": session.job_title,
    }


@router.post("/session/reset")
def reset_session(user_row=auth.CurrentUser) -> dict:
    """归档当前会话并回到欢迎首页。"""
    session_store.archive_current(user_row["id"])
    return {"ok": True}


@router.get("/session/history")
def session_history(user_row=auth.CurrentUser, limit: int = 50) -> dict:
    """某用户的面试历史（复盘/继续）。"""
    return {"items": session_store.list_history(user_row["id"], limit=limit)}


@router.post("/chat")
async def chat(body: ChatBody, user_row=auth.CurrentUser):
    """SSE 流式对话：推进当前会话（无会话时自动建辅导答疑）。"""
    session = session_store.load_active_session(user_row["id"])
    if session is None:
        session = InterviewSession("coach")
        session.display_history = [["assistant", prompts.COACH_GREETING]]
        session_store.start_session(user_row["id"], session)

    gen = session.handle_stream(body.message)
    session.display_history.append(["user", body.message])

    def _next_chunk():
        try:
            return next(gen)
        except StopIteration:
            return None

    async def _event_stream():
        try:
            while True:
                delta = await asyncio.to_thread(_next_chunk)
                if delta is None:
                    break
                if delta:
                    yield _sse(
                        {"type": "delta", "content": delta}
                    )
            # 流结束：先把助手回复补进展示历史，再持久化会话（保证刷新后不丢最后一条）
            session.display_history.append(
                ["assistant", session.messages[-1]["content"] if session.messages else ""]
            )
            try:
                session_store.save_session(user_row["id"], session)
            except Exception:
                logger.exception("会话持久化失败（user=%s）", user_row["username"])
            report = None
            if session.finished and session.messages and session.messages[-1].get("role") == "assistant":
                report = session.messages[-1]["content"]
            yield _sse(
                {
                    "type": "done",
                    "finished": session.finished,
                    "mode": session.mode,
                    "history": session.history_for_display(),
                    "report": report,
                }
            )
        except Exception as e:  # noqa: BLE001 - 流式里兜底所有异常并告知前端
            logger.exception("聊天流式处理异常")
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

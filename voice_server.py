"""面试官小P 语音通话服务（FastAPI + WebSocket + edge-tts 神经语音）。

链路设计（实现"像打电话一样"的实时对话）：
- 浏览器端：连续语音识别（Web Speech API），每识别出一句话发给本服务；
  播报期间用带回声抑制的麦克风音量检测（VAD）实现"开口即打断"。
- 本服务：DeepSeek 流式回复按句子切分，逐句用 edge-tts 合成 MP3 推回，
  浏览器按序播放，边生成边播报。

WebSocket 协议（JSON 文本帧）：
  客户端 -> 服务端：{"type":"text","content":...} / {"type":"stop"}
  服务端 -> 客户端：
    {"type":"delta","content":...}                      文本增量（状态显示）
    {"type":"audio_start","sid":N,"text":...}            一句话音频开始
    {"type":"audio","sid":N,"data":"<base64 mp3>"}      音频分片
    {"type":"audio_end","sid":N}                         一句话音频结束
    {"type":"tts_error","sid":N}                         TTS 失败，浏览器回退本地语音
    {"type":"done"} / {"type":"cancelled"} / {"type":"error","message":...}

启动：
    python -m uvicorn voice_server:app --host 127.0.0.1 --port 8765
"""
import asyncio
import base64
import json
import logging
import re
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

import app.db as db
from app import config
from app.agent import llm
from app.agent.coach import InterviewSession
from app.scheduler import setup_logging

try:
    import edge_tts
except ImportError:  # 未安装时降级：浏览器回退 speechSynthesis
    edge_tts = None

logger = logging.getLogger("voice_server")

_END = object()  # 生成器结束哨兵（StopIteration 不能跨 asyncio.to_thread 传播）
_SENT_END = re.compile(r"[。！？；\n.!?;]")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    db.init_db()
    if not llm.is_api_key_configured():
        logger.warning("未检测到有效的 DEEPSEEK_API_KEY，语音对话将无法使用（请在 .env 中配置）")
    yield


app = FastAPI(title="面试官小P 语音通话服务", lifespan=lifespan)


def maybe_switch_to_mock(session: InterviewSession, text: str) -> InterviewSession:
    """首条消息说"开始面试/模拟面试"时，从答疑模式切换到模拟面试模式。"""
    if (
        session.mode == "coach"
        and len(session.messages) <= 1
        and ("开始面试" in text or "模拟面试" in text)
    ):
        return InterviewSession("mock")
    return session


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


def _split_sentences(buf: str) -> tuple[list[str], str]:
    """按句子边界切分，返回（完整句子列表, 剩余缓冲）。"""
    out: list[str] = []
    while True:
        m = _SENT_END.search(buf)
        if not m:
            break
        idx = m.end()
        out.append(buf[:idx].strip())
        buf = buf[idx:]
    return out, buf


async def _synthesize(ws: WebSocket, sid: int, sentence: str) -> bool:
    """把一句话用 edge-tts 合成 MP3 并逐片推送；失败发送 tts_error，返回是否成功。"""
    if not sentence.strip():
        return False
    try:
        await ws.send_text(
            json.dumps({"type": "audio_start", "sid": sid, "text": sentence}, ensure_ascii=False)
        )
        if edge_tts is None or config.VOICE_TTS == "local":
            await ws.send_text(json.dumps({"type": "tts_error", "sid": sid}))
            return False
        comm = edge_tts.Communicate(
            sentence,
            voice=config.VOICE_NAME,
            rate=config.VOICE_RATE,
            pitch=config.VOICE_PITCH,
        )
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                data = base64.b64encode(chunk["data"]).decode("ascii")
                await ws.send_text(json.dumps({"type": "audio", "sid": sid, "data": data}))
        await ws.send_text(json.dumps({"type": "audio_end", "sid": sid}))
        return True
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("TTS 合成失败（sid=%s）: %s", sid, e)
        try:
            await ws.send_text(json.dumps({"type": "tts_error", "sid": sid}))
        except Exception:
            pass
        return False


async def _produce(ws: WebSocket, session: InterviewSession, text: str) -> None:
    """把用户文本推进会话，流式生成文本+语音；任务可被取消（barge-in）。"""
    gen = session.handle_stream(text)

    def _next_chunk():
        try:
            return next(gen)
        except StopIteration:
            return _END

    sid = 0
    buf = ""
    state = {"tts_ok": True}  # 共享降级标记：第一次在线合成失败后，后续走本地语音
    tts_tasks: list[asyncio.Task] = []

    async def _tts(my_sid: int, chunk: str) -> None:
        """合成一句话：在线失败后本回复剩余句子直接降级。"""
        if state["tts_ok"]:
            ok = await _synthesize(ws, my_sid, chunk)
            if not ok:
                state["tts_ok"] = False
        else:
            await ws.send_text(
                json.dumps({"type": "audio_start", "sid": my_sid, "text": chunk}, ensure_ascii=False)
            )
            await ws.send_text(json.dumps({"type": "tts_error", "sid": my_sid}))

    try:
        while True:
            # OpenAI 同步流在生成器内部阻塞，放到线程执行，避免卡住事件循环
            delta = await asyncio.to_thread(_next_chunk)
            if delta is _END:
                break
            if delta:
                await ws.send_text(json.dumps({"type": "delta", "content": delta}, ensure_ascii=False))
                buf += delta
                sentences, buf = _split_sentences(buf)
                # 每句一个任务并发合成（浏览器按 sid 顺序播放），避免句与句之间停顿
                for s in sentences:
                    if not s.strip():
                        continue
                    sid += 1
                    tts_tasks.append(asyncio.create_task(_tts(sid, s)))
        if buf.strip():
            sid += 1
            tts_tasks.append(asyncio.create_task(_tts(sid, buf)))
        if tts_tasks:
            await asyncio.gather(*tts_tasks)
        await ws.send_text(json.dumps({"type": "done"}))
    except asyncio.CancelledError:
        for t in tts_tasks:
            t.cancel()
        logger.info("回复生成被用户打断")
        try:
            await ws.send_text(json.dumps({"type": "cancelled"}))
        except Exception:
            pass
    except Exception as e:
        for t in tts_tasks:
            t.cancel()
        logger.exception("回复生成失败")
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass


@app.websocket("/ws/voice")
async def voice(ws: WebSocket) -> None:
    await ws.accept()
    session = InterviewSession("coach")  # 默认辅导答疑；首条说"开始面试"则切换模拟面试
    generation: asyncio.Task | None = None
    logger.info("语音通话已接通")
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "stop":
                if generation is not None and not generation.done():
                    generation.cancel()
                continue
            if mtype != "text":
                continue
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            session = maybe_switch_to_mock(session, text)
            # 用户开口 → 取消上一轮未完成的生成（barge-in）
            if generation is not None and not generation.done():
                generation.cancel()
            generation = asyncio.create_task(_produce(ws, session, text))
    except WebSocketDisconnect:
        logger.info("语音通话已断开")
    finally:
        if generation is not None and not generation.done():
            generation.cancel()


if __name__ == "__main__":
    uvicorn.run(app, host=config.VOICE_HOST, port=config.VOICE_PORT)

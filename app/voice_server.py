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
    python -m uvicorn app.voice_server:app --host 127.0.0.1 --port 8765
或安装后直接运行：
    xiaop-voice
"""

import asyncio
import base64
import json
import logging
import re
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import app.db as db
from app import config, prompts
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

#: edge-tts 原始音频块非常小（约 0.13s/块），逐块推送会让浏览器频繁解码调度导致卡顿；
#: 服务端聚合成 ~8KB（约 1.5-2s 语音）再推一块，既保持"边合成边播"，又大幅减少播放单元数。
TTS_CHUNK_TARGET = 8 * 1024

#: 多句合并合成：每个 edge-tts 连接承载约 2-3 句话（连接数少 5 倍，失败面小；
#: 句与句之间的语气不再被割裂，听起来更自然、不机械）。
TTS_FIRST_CHARS = 45  # 首块阈值：尽快开播
TTS_CHUNK_CHARS = 90  # 后续块阈值：约 2-3 句
# 串行合成：每段 6-8 秒音频，合成快于播放所以不会断流；
# 且"某段失败→剩余段整体降级本地语音"的判定是确定的，不会出现混合音色。
TTS_MAX_CONCURRENCY = 1

#: edge-tts 跨回复熔断：连续失败 N 次后暂停在线合成一段时间，直接降级本地语音（避免每次干等超时）
TTS_CIRCUIT_FAILS = 2
TTS_CIRCUIT_COOLDOWN = 60
_tts_circuit = {"fails": 0, "open_until": 0.0}


def _tts_circuit_open() -> bool:
    return time.monotonic() < _tts_circuit["open_until"]


def _tts_note_failure() -> None:
    _tts_circuit["fails"] += 1
    if _tts_circuit["fails"] >= TTS_CIRCUIT_FAILS:
        _tts_circuit["open_until"] = time.monotonic() + TTS_CIRCUIT_COOLDOWN
        logger.warning(
            "edge-tts 连续失败 %s 次，熔断 %s 秒，期间直接使用浏览器本地语音",
            TTS_CIRCUIT_FAILS,
            TTS_CIRCUIT_COOLDOWN,
        )


def _tts_note_success() -> None:
    _tts_circuit["fails"] = 0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    db.init_db()
    if not llm.is_api_key_configured():
        logger.warning("未检测到有效的 DEEPSEEK_API_KEY，语音对话将无法使用（请在 .env 中配置）")
    if config.VOICE_TTS == "cosyvoice" and not config.DASHSCOPE_API_KEY:
        logger.warning("VOICE_TTS=cosyvoice 但未配置 DASHSCOPE_API_KEY，语音将回退浏览器本地语音")
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


@app.get("/", response_class=HTMLResponse)
async def voice_page() -> str:
    """独立语音通话页（方案A）：由 FastAPI 直接托管，摆脱 Streamlit iframe/rerun 限制。"""
    # 每次请求实时读取，前端改动即时生效（无需重启语音服务）
    html = (Path(__file__).resolve().parent / "ui" / "voice_page.html").read_text(encoding="utf-8")
    return html.replace("__VAD_THRESHOLD__", str(config.VOICE_VAD_THRESHOLD)).replace(
        "__WEB_URL__", config.WEB_URL
    )


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


#: 模型常见舞台指示，如（点头微笑）（皱眉）——TTS 会原样念出来，非常出戏。
#: 只去掉"纯中文、短、不含数字/字母"的括号内容，保留 (O(n))、TCP/IP 这类技术内容。
_TTS_STAGE_DIR = re.compile(r"[（(][^（）()A-Za-z0-9]{0,8}[）)]")


def _clean_tts_text(text: str) -> str:
    """合成前清洗：去掉舞台指示等不应被朗读的内容。"""
    return _TTS_STAGE_DIR.sub("", text or "").strip()


def _cosyvoice_request(sentence: str) -> bytes | None:
    """同步请求阿里云百炼 CosyVoice，返回音频字节；失败返回 None（在线程中调用）。"""
    key = config.DASHSCOPE_API_KEY
    if not key:
        return None
    payload = {
        "model": config.COSYVOICE_MODEL,
        "input": {
            "text": sentence,
            "voice": config.COSYVOICE_VOICE,
            "format": config.COSYVOICE_FORMAT,
            "sample_rate": config.COSYVOICE_SAMPLE_RATE,
            "rate": config.COSYVOICE_RATE,
            "pitch": config.COSYVOICE_PITCH,
        },
    }
    try:
        resp = requests.post(
            "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("CosyVoice 请求失败: %s", e)
        return None
    audio = (data.get("output") or {}).get("audio") or {}
    if audio.get("data"):
        try:
            return base64.b64decode(audio["data"])
        except Exception as e:
            logger.warning("CosyVoice 音频解码失败: %s", e)
            return None
    url = audio.get("url")
    if not url:
        logger.warning("CosyVoice 响应缺少音频: %s", str(data)[:200])
        return None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.warning("CosyVoice 音频下载失败: %s", e)
        return None


async def _cosyvoice_synthesize(sentence: str) -> bytes | None:
    """异步包装：在线程中请求 CosyVoice，返回完整音频字节。"""
    return await asyncio.to_thread(_cosyvoice_request, sentence)


async def _synthesize(ws: WebSocket, state: dict, sentence: str) -> bool:
    """把一段文字用 edge-tts 合成 MP3，并按聚合后的音频单元推送（边合成边播）。

    edge-tts 的小音频块会先在服务端聚合成 ~8KB 的大单元（audio_start/audio/audio_end 三连），
    浏览器按 sid 排序零间隙播放，避免小块频繁解码导致的卡顿；
    连接级失败（尚未出音频）会重试一次；全部失败才发送 audio_start + tts_error 回退本地语音。
    state 需含 "sid" 计数键（同一回复内全局递增，保证 sid 唯一且有序）。
    """
    if not sentence.strip():
        return False

    def _next_sid() -> int:
        state["sid"] += 1
        return state["sid"]

    async def _send_fail() -> None:
        my_sid = _next_sid()
        await ws.send_text(
            json.dumps({"type": "audio_start", "sid": my_sid, "text": sentence}, ensure_ascii=False)
        )
        await ws.send_text(json.dumps({"type": "tts_error", "sid": my_sid}))

    async def _flush(buf: list[bytes], text: str) -> bool:
        if not buf:
            return False
        my_sid = _next_sid()
        data = base64.b64encode(b"".join(buf)).decode("ascii")
        await ws.send_text(
            json.dumps({"type": "audio_start", "sid": my_sid, "text": text}, ensure_ascii=False)
        )
        await ws.send_text(json.dumps({"type": "audio", "sid": my_sid, "data": data}))
        await ws.send_text(json.dumps({"type": "audio_end", "sid": my_sid}))
        return True

    async def _try_once() -> tuple[bool, bool]:
        """尝试一次在线合成，返回 (是否已推送过音频, 是否完整成功)。"""
        if config.VOICE_TTS == "cosyvoice":
            audio = await _cosyvoice_synthesize(sentence)
            if not audio:
                return False, False
            # CosyVoice 一次返回整段音频，作为一个播放单元推送
            await _flush([audio], sentence)
            return True, True
        if edge_tts is None:
            return False, False
        comm = edge_tts.Communicate(
            sentence,
            voice=config.VOICE_NAME,
            rate=config.VOICE_RATE,
            pitch=config.VOICE_PITCH,
        )
        buf: list[bytes] = []
        size = 0
        sent = False
        try:
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf.append(chunk["data"])
                    size += len(chunk["data"])
                    if size >= TTS_CHUNK_TARGET:
                        sent = await _flush(buf, sentence) or sent
                        buf = []
                        size = 0
            if buf:
                sent = await _flush(buf, sentence) or sent
            return sent, True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("TTS 在线合成失败（sentence=%s）: %s", sentence[:30], e)
            if buf and not sent:
                # 已合成出部分音频：先推出去，避免浏览器整句重播造成重复/卡顿
                try:
                    sent = await _flush(buf, sentence)
                except Exception:
                    sent = False
            return sent, False

    try:
        if config.VOICE_TTS not in ("edge", "cosyvoice") or _tts_circuit_open():
            await _send_fail()
            return False
        if config.VOICE_TTS == "cosyvoice" and not config.DASHSCOPE_API_KEY:
            await _send_fail()
            return False
        sent_any, ok = await _try_once()
        if not ok and not sent_any:
            # 连接级失败且完全没出音频：重试一次（edge-tts 多为瞬时连接失败）
            sent_any, ok = await _try_once()
        if ok:
            _tts_note_success()
            return True
        _tts_note_failure()
        if not sent_any:
            with suppress(Exception):
                await _send_fail()
        return False
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("TTS 处理异常（sentence=%s）", sentence[:30])
        _tts_note_failure()
        with suppress(Exception):
            await _send_fail()
        return False


async def _produce(ws: WebSocket, session: InterviewSession, text: str) -> None:
    """把用户文本推进会话，流式生成文本+语音；任务可被取消（barge-in）。"""
    gen = session.handle_stream(text)

    def _next_chunk():
        try:
            return next(gen)
        except StopIteration:
            return _END

    buf = ""  # 未切分句的流式残余
    tts_buf = ""  # 待合成的多句缓冲（合并后一次合成，减少连接数、保留句间语气）
    state = {"tts_ok": True, "sid": 0}  # 共享降级标记 + 音频单元 sid 分配器
    tts_tasks: list[asyncio.Task] = []
    sem = asyncio.Semaphore(TTS_MAX_CONCURRENCY)

    def _next_sid() -> int:
        state["sid"] += 1
        return state["sid"]

    def _flush_tts() -> None:
        nonlocal tts_buf
        if not tts_buf.strip():
            return
        tts_tasks.append(asyncio.create_task(_tts(tts_buf)))
        tts_buf = ""

    async def _tts(chunk: str) -> None:
        """合成一段（约 2-3 句）：限制并发；在线失败后本回复剩余段直接降级本地语音。"""
        async with sem:
            chunk = _clean_tts_text(chunk)
            if not chunk:
                return
            if state["tts_ok"]:
                ok = await _synthesize(ws, state, chunk)
                if not ok:
                    state["tts_ok"] = False
            else:
                my_sid = _next_sid()
                await ws.send_text(
                    json.dumps(
                        {"type": "audio_start", "sid": my_sid, "text": chunk}, ensure_ascii=False
                    )
                )
                await ws.send_text(json.dumps({"type": "tts_error", "sid": my_sid}))

    try:
        # 先告知浏览器本段回复第一个音频块的 sid，即使音频块乱序到达也能按序播放
        await ws.send_text(json.dumps({"type": "reply_start", "first_sid": state["sid"] + 1}))
        while True:
            # OpenAI 同步流在生成器内部阻塞，放到线程执行，避免卡住事件循环
            delta = await asyncio.to_thread(_next_chunk)
            if delta is _END:
                break
            if delta:
                await ws.send_text(
                    json.dumps({"type": "delta", "content": delta}, ensure_ascii=False)
                )
                buf += delta
                sentences, buf = _split_sentences(buf)
                # 多句合并合成：首块尽快开播，后续按 ~90 字合并，减少连接数与句间语气割裂
                for s in sentences:
                    if not s.strip():
                        continue
                    tts_buf += s
                    limit = TTS_FIRST_CHARS if not tts_tasks else TTS_CHUNK_CHARS
                    if len(tts_buf) >= limit:
                        _flush_tts()
        if buf.strip():
            tts_buf += buf
        _flush_tts()
        if tts_tasks:
            await asyncio.gather(*tts_tasks)
        await ws.send_text(json.dumps({"type": "done"}))
    except asyncio.CancelledError:
        for t in tts_tasks:
            t.cancel()
        logger.info("回复生成被用户打断")
        with suppress(Exception):
            await ws.send_text(json.dumps({"type": "cancelled"}))
    except Exception as e:
        for t in tts_tasks:
            t.cancel()
        logger.exception("回复生成失败")
        with suppress(Exception):
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))


async def _produce_greeting(ws: WebSocket) -> None:
    """接通后先播报开场白，像真实通话一样不等用户开口。可被 barge-in 取消。"""
    sentences, rest = _split_sentences(prompts.VOICE_GREETING)
    if rest.strip():
        sentences.append(rest.strip())
    state = {"sid": 0}
    try:
        await ws.send_text(json.dumps({"type": "reply_start", "first_sid": 1}))
        # 文本先逐句展示，音频整段一次合成（句间语气连贯，不机械）
        for s in sentences:
            if not s.strip():
                continue
            await ws.send_text(json.dumps({"type": "delta", "content": s}, ensure_ascii=False))
        await _synthesize(ws, state, prompts.VOICE_GREETING)
        await ws.send_text(json.dumps({"type": "done"}))
    except asyncio.CancelledError:
        logger.info("开场白被用户打断")
        with suppress(Exception):
            await ws.send_text(json.dumps({"type": "cancelled"}))


@app.websocket("/ws/voice")
async def voice(ws: WebSocket) -> None:
    await ws.accept()
    session = InterviewSession("coach")  # 默认辅导答疑；首条说"开始面试"则切换模拟面试
    generation: asyncio.Task | None = None
    logger.info("语音通话已接通")
    generation = asyncio.create_task(_produce_greeting(ws))
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


def main() -> None:
    """命令行入口：启动语音通话服务。"""
    uvicorn.run(app, host=config.VOICE_HOST, port=config.VOICE_PORT)


if __name__ == "__main__":
    main()

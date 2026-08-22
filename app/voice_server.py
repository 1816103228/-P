"""面试官小P 统一 Web 服务（FastAPI + WebSocket + edge-tts 神经语音）。

多用户改造后为唯一后端：同时提供
- REST API（认证 / 会话与聊天 / 题库 / 定制面试，见 app/routers/）；
- Vue3 前端静态托管（frontend/dist，SPA history 回退）；
- 语音通话 WebSocket（按用户认证与持久化）。

语音链路设计（实现"像打电话一样"的实时对话）：
- 浏览器端：连续语音识别（Web Speech API），每识别出一句话发给本服务；
  播报期间用带回声抑制的麦克风音量检测（VAD）实现"开口即打断"。
- 本服务：DeepSeek 流式回复按句子切分，逐句用 edge-tts 合成 MP3 推回，
  浏览器按序播放，边生成边播报。

WebSocket 协议（JSON 文本帧，需 ?token=<登录令牌>）：
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

import time

import requests
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import app.db as db
import app.session_store as session_store
import app.voice_store as voice_store
import asyncio
import base64
import json
import logging
import re
from app import auth, config, prompts
from app.agent import llm
from app.agent.coach import InterviewSession
from app.asr_client import DashScopeASR
from app.routers import auth as auth_api
from app.routers import custom as custom_api
from app.routers import questions as questions_api
from app.routers import session as session_api
from app.scheduler import setup_logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

try:
    import edge_tts
except ImportError:  # 未安装时降级：浏览器回退 speechSynthesis
    edge_tts = None

logger = logging.getLogger("voice_server")

_END = object()  # 生成器结束哨兵（StopIteration 不能跨 asyncio.to_thread 传播）
_SENT_END = re.compile(r"[。！？；\n.!?;]")

#: 首条消息触发"模拟面试"的明确意图（不含"模拟面试是什么"这类提问）
_MOCK_START_RE = re.compile(
    r"开始面试"
    r"|开始模拟面试"
    r"|^(?:我(?:想|要))?模拟面试(?:吧|一下|下|了)?$"
    r"|(?:我想|我要|来|做|进行|试试|开启|帮我|开始一[场次]).{0,4}模拟面试"
)

#: edge-tts 原始音频块非常小（约 0.13s/块），逐块推送会让浏览器频繁解码调度导致卡顿；
#: 服务端聚合成 ~8KB（约 1.5-2s 语音）再推一块，既保持"边合成边播"，又大幅减少播放单元数。
TTS_CHUNK_TARGET = 8 * 1024

#: 多句合并合成：每个 edge-tts 连接承载约 2-3 句话（连接数少 5 倍，失败面小；
#: 句与句之间的语气不再被割裂，听起来更自然、不机械）。
TTS_FIRST_CHARS = 45  # 首块阈值：尽快开播
TTS_CHUNK_CHARS = 90  # 后续块阈值：约 2-3 句
# 并行合成：多段同时交给 CosyVoice（每段请求 4-6 秒），整条回复时间大幅缩短；
# 合成快于播放，浏览器队列不会断流。失败降级仍按"段"判定，个别失败只影响该段。
TTS_MAX_CONCURRENCY = 3

#: edge-tts 跨回复熔断：连续失败 N 次后暂停在线合成一段时间，直接降级本地语音（避免每次干等超时）
#: 熔断状态放在每个连接的 state 字典里（每连接独立），避免一个连接的网络抖动拖累其他连接
TTS_CIRCUIT_FAILS = 2
TTS_CIRCUIT_COOLDOWN = 60

#: ASR 断线自动重连：初始延迟与最大退避（秒）。
#: 阿里云实时识别是长连接 WebSocket，网络抖动会断开（心跳 PONG 超时），需要自动恢复
ASR_RETRY_DELAY = 3.0
ASR_RETRY_MAX = 20.0


def _tts_circuit_open(state: dict) -> bool:
    return time.monotonic() < state.get("tts_open_until", 0.0)


def _tts_note_failure(state: dict) -> None:
    state["tts_fails"] = state.get("tts_fails", 0) + 1
    if state["tts_fails"] >= TTS_CIRCUIT_FAILS:
        state["tts_open_until"] = time.monotonic() + TTS_CIRCUIT_COOLDOWN
        logger.warning(
            "本连接 edge-tts 连续失败 %s 次，熔断 %s 秒，期间直接使用浏览器本地语音",
            TTS_CIRCUIT_FAILS,
            TTS_CIRCUIT_COOLDOWN,
        )


def _tts_note_success(state: dict) -> None:
    state["tts_fails"] = 0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    db.init_db()
    if not llm.is_api_key_configured():
        logger.warning("未检测到有效的 DEEPSEEK_API_KEY，语音对话将无法使用（请在 .env 中配置）")
    if config.VOICE_TTS == "cosyvoice" and not config.DASHSCOPE_API_KEY:
        logger.warning("VOICE_TTS=cosyvoice 但未配置 DASHSCOPE_API_KEY，语音将回退浏览器本地语音")
    yield


app = FastAPI(title="面试官小P", lifespan=lifespan)
app.include_router(auth_api.router)
app.include_router(session_api.router)
app.include_router(questions_api.router)
app.include_router(custom_api.router)

# 静态资源：虚拟人物头像（聊天页 / 语音页共用）
app.mount(
    "/assets",
    StaticFiles(directory=Path(__file__).resolve().parent / "ui" / "assets"),
    name="assets",
)


def maybe_switch_to_mock(session: InterviewSession, text: str) -> InterviewSession:
    """首条消息说"开始面试/模拟面试"时，从答疑模式切换到模拟面试模式。"""
    # 只匹配明确的"开始"意图，避免首条消息只是提问"模拟面试是什么"就误切换
    match = _MOCK_START_RE.search(text)
    if session.mode == "coach" and len(session.messages) <= 1 and match is not None:
        return InterviewSession("mock", persona=session.persona)
    return session


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config/voice")
async def voice_config() -> dict:
    """前端语音页所需运行时配置（VAD 阈值等，替代旧 HTML 模板替换注入）。"""
    return {
        "vad_threshold": config.VOICE_VAD_THRESHOLD,
        "vad_hits": config.VOICE_VAD_HITS,
        "vad_quiet_frames": config.VOICE_VAD_QUIET_FRAMES,
        "vad_noise_margin": config.VOICE_VAD_NOISE_MARGIN,
        "tts": config.VOICE_TTS,
    }


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
        if config.VOICE_TTS == "cosyvoice" and state.get("tts_voice") != "edge":
            audio = await _cosyvoice_synthesize(sentence)
            if audio:
                state["tts_voice"] = "cosyvoice"
                # CosyVoice 一次返回整段音频，作为一个播放单元推送
                await _flush([audio], sentence)
                return True, True
            # CosyVoice 不可用（欠费/配额/网络）→ 本回复后续段落统一用 edge-tts，
            # 避免同一条回复里混两种音色（听起来像"两个声音"）
            state["tts_voice"] = "edge"
            logger.warning("CosyVoice 不可用，本回复后续段落降级 edge-tts 保持音色一致")
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
        state.setdefault("tts_fails", 0)
        state.setdefault("tts_open_until", 0.0)
        if config.VOICE_TTS not in ("edge", "cosyvoice") or _tts_circuit_open(state):
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
            _tts_note_success(state)
            return True
        _tts_note_failure(state)
        if not sent_any:
            with suppress(Exception):
                await _send_fail()
        return False
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("TTS 处理异常（sentence=%s）", sentence[:30])
        _tts_note_failure(state)
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


async def _produce_greeting(ws: WebSocket, greeting: str = prompts.VOICE_GREETING) -> None:
    """接通后先播报开场白，像真实通话一样不等用户开口。可被 barge-in 取消。"""
    sentences, rest = _split_sentences(greeting)
    if rest.strip():
        sentences.append(rest.strip())
    state = {"sid": 0}
    try:
        await ws.send_text(json.dumps({"type": "reply_start", "first_sid": 1}))
        # 文本逐句展示；语音整段一次合成：同一句开场白只有一个音色，
        # 不会出现"前一句 CosyVoice、后一句 edge-tts"的两种声音
        for s in sentences:
            if not s.strip():
                continue
            await ws.send_text(json.dumps({"type": "delta", "content": s}, ensure_ascii=False))
        await _synthesize(ws, state, greeting.strip())
        await ws.send_text(json.dumps({"type": "done"}))
    except asyncio.CancelledError:
        logger.info("开场白被用户打断")
        with suppress(Exception):
            await ws.send_text(json.dumps({"type": "cancelled"}))


def _build_custom_greeting(custom: dict) -> str:
    """定制面试接通时的开场白：告知岗位、题数与流程。"""
    title = (custom.get("job_title") or "").strip() or "自定义岗位"
    count = len(custom.get("questions") or [])
    return (
        f"你好，我是面试官小P。已为你准备好「{title}」的定制面试，共 {count} 道题。"
        "先做个 1 分钟自我介绍吧，我会由浅入深逐题提问，全程点评，"
        "全部结束后为你输出评分报告。"
    )


def _clear_custom_when_finished(user_id: int, session: InterviewSession) -> None:
    """定制面试正常结束后，清除该用户待执行状态，避免下次接通重复同一套题。"""
    if (
        getattr(session, "finished", False)
        and session.mode == "mock"
        and getattr(session, "custom_questions", None)
    ):
        voice_store.clear_custom_interview(user_id)


REOPEN_GREETING = (
    "欢迎回来，我们继续刚才的对话。你可以直接提问，也可以说“开始面试”开始一场新的模拟面试。"
)


@app.websocket("/ws/voice")
async def voice(ws: WebSocket) -> None:
    # 多用户认证：WebSocket 无法携带请求头，用查询参数 ?token=...
    user = auth.resolve_token_user(ws.query_params.get("token"))
    if user is None:
        await ws.close(code=4401, reason="未登录或登录已过期")
        return
    user_id = user["id"]
    await ws.accept()
    persona = user.get("persona") or ""
    custom = voice_store.load_custom_interview(user_id)
    if custom:
        # 已准备定制面试 → 接通即进入模拟面试，用定制题目
        session = InterviewSession(
            "mock",
            questions=custom["questions"],
            job_title=custom.get("job_title", ""),
            jd=custom.get("jd", ""),
            persona=persona,
        )
        greeting = _build_custom_greeting(custom)
    else:
        # 该用户未结束的活跃会话 → 接着聊；否则默认辅导答疑
        session = session_store.load_active_session(user_id)
        if session is not None and not session.finished:
            greeting = REOPEN_GREETING
        else:
            session = InterviewSession("coach", persona=persona)
            greeting = prompts.VOICE_GREETING
    # 新建会话（语音发起的默认答疑 / 语音定制）落库，供文字版与后续语音共享
    if getattr(session, "session_id", None) is None:
        session_store.start_session(user_id, session)
    generation: asyncio.Task | None = None
    loop = asyncio.get_running_loop()
    asr: DashScopeASR | None = None
    asr_retry: asyncio.Task | None = None  # 断线自动重连监督任务
    _asr_stopping = False
    _audio_seen = False  # 是否收到过麦克风音频帧（诊断用）

    async def _on_asr_sentence(text: str) -> None:
        """ASR 识别出完整句子 → 回传前端（前端按用户输入处理）。"""
        try:
            await ws.send_text(
                json.dumps({"type": "asr_text", "content": text}, ensure_ascii=False)
            )
        except Exception:
            logger.debug("asr_text 发送失败（连接可能已断开）")

    async def _on_asr_partial(text: str) -> None:
        """ASR 中间结果 → 回传前端，用于"开口即打断"（不等整句识别完）。"""
        with suppress(Exception):
            await ws.send_text(
                json.dumps({"type": "asr_partial", "content": text}, ensure_ascii=False)
            )

    async def _on_asr_error(code=None, message=None) -> None:
        """ASR 识别流中途出错：置 None，由监督任务自动重连（指数退避）。"""
        nonlocal asr
        asr = None
        detail = f"{code}: {message}" if code else (message or "未知错误")
        logger.warning("ASR 识别流中断: %s", detail)
        with suppress(Exception):
            await ws.send_text(
                json.dumps(
                    {
                        "type": "asr_error",
                        "message": f"语音识别中断（{detail}），正在自动重试…",
                    },
                    ensure_ascii=False,
                )
            )

    async def _start_asr(sr: int) -> bool:
        """创建并启动 DashScope ASR；成功回传 asr_ready。"""
        nonlocal asr
        try:
            asr = DashScopeASR(
                loop,
                _on_asr_sentence,
                on_partial=_on_asr_partial,
                on_error=_on_asr_error,
                sample_rate=sr,
            )
        except Exception as e:
            asr = None
            logger.exception("ASR 实例创建失败: %s", e)
            return False
        if asr.start():
            logger.info("ASR 已启动 (sample_rate=%s)", sr)
            with suppress(Exception):
                await ws.send_text(json.dumps({"type": "asr_ready"}, ensure_ascii=False))
            return True
        asr = None
        return False

    async def _asr_supervisor(sr: int) -> None:
        """ASR 断线后自动重连（指数退避 3s→20s），直到通话结束。"""
        delay = ASR_RETRY_DELAY
        while not _asr_stopping:
            await asyncio.sleep(delay)
            if _asr_stopping:
                return
            if asr is None:
                ok = await _start_asr(sr)
                delay = ASR_RETRY_DELAY if ok else min(delay * 2, ASR_RETRY_MAX)
            else:
                delay = ASR_RETRY_DELAY

    logger.info("语音通话已接通")
    generation = asyncio.create_task(_produce_greeting(ws, greeting))
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
            if mtype == "asr_start":
                # 前端告知采集采样率 → 创建 DashScope 流式 ASR；
                # 后续断线由 _asr_supervisor 自动重连，无需前端反复重发
                if asr is None and (asr_retry is None or asr_retry.done()):
                    sr = int(msg.get("sample_rate") or config.ASR_SAMPLE_RATE)
                    ok = await _start_asr(sr)
                    if not ok:
                        await ws.send_text(
                            json.dumps(
                                {
                                    "type": "asr_error",
                                    "message": "语音识别启动失败，自动重试中…",
                                },
                                ensure_ascii=False,
                            )
                        )
                    asr_retry = asyncio.create_task(_asr_supervisor(sr))
                continue
            if mtype == "audio":
                # 前端 PCM 音频帧 → ASR 流式识别
                if asr is not None:
                    if not _audio_seen:
                        _audio_seen = True
                        logger.info("收到麦克风音频帧（ASR 识别中）")
                    try:
                        data = base64.b64decode(msg.get("data") or "")
                        asr.send(data)
                    except Exception:
                        logger.exception("audio 帧处理失败")
                elif not _audio_seen:
                    _audio_seen = True
                    logger.info("收到麦克风音频帧但 ASR 未就绪（自动重连中，稍候恢复）")
                continue
            if mtype != "text":
                continue
            text = (msg.get("content") or "").strip()
            if not text:
                continue
            new_session = maybe_switch_to_mock(session, text)
            if new_session is not session:
                # 从答疑切换为模拟面试：新会话入库，替换当前会话
                session = new_session
                session_store.start_session(user_id, session)
            # 用户开口 → 取消上一轮未完成的生成（barge-in）
            if generation is not None and not generation.done():
                generation.cancel()
            task = asyncio.create_task(_produce(ws, session, text))

            def _on_produce_done(t, s=session):
                # 定制面试结束（输出总结报告）后清除待执行状态，避免下次接通重复同一套题
                _clear_custom_when_finished(user_id, s)
                # 每次回复完成即持久化会话状态（断线/刷新可恢复）
                with suppress(Exception):
                    session_store.save_session(user_id, s)

            task.add_done_callback(_on_produce_done)
            generation = task
    except WebSocketDisconnect:
        logger.info("语音通话已断开")
    finally:
        _asr_stopping = True
        if asr_retry is not None:
            asr_retry.cancel()
        if generation is not None and not generation.done():
            generation.cancel()
        if asr is not None:
            asr.stop()


#: Vue3 前端构建产物目录（frontend/dist）；未构建时前端路由返回提示
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_fallback(full_path: str):
    """托管 Vue3 SPA：命中文件返回文件，其余交给 index.html（history 路由回退）。

    必须注册在所有具体路由之后（本文件最末），确保 /api、/ws/voice、/assets 优先匹配。
    """
    if full_path.startswith(("api/", "ws/")):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if full_path:
        candidate = (_FRONTEND_DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(_FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
    index = _FRONTEND_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        {"detail": "前端尚未构建，请先在 frontend/ 下运行 npm run build"},
        status_code=404,
    )


def main() -> None:
    """命令行入口：启动统一 Web 服务（Vue3 前端 + REST + 语音，单端口）。"""
    uvicorn.run(app, host=config.APP_HOST, port=config.APP_PORT)


if __name__ == "__main__":
    main()

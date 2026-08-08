"""DashScope 实时语音识别（Paraformer）客户端。

替代 Chrome 内置 SpeechRecognition（依赖 Google 服务，国内网络不可用）。
前端把麦克风 PCM 音频流经 WebSocket 送到本服务，这里用 dashscope SDK
做流式识别，把"句子结束"的识别结果通过回调桥接到 asyncio 事件循环，
再回传给前端（{"type":"asr_text","content":...}）。

- 每通语音通话创建一个 DashScopeASR（一次 start 持续识别，服务端 VAD 自动断句）；
- 前端在小P播报时暂停发送音频（防回声被识别），用户开口（VAD 打断）后恢复发送；
- SDK 回调运行在内部 worker 线程，用 run_coroutine_threadsafe 桥接到事件循环。
"""

import asyncio
import logging

import dashscope
from dashscope.audio.asr import Recognition, RecognitionCallback

from app import config

logger = logging.getLogger("interview_coach.asr")


class _AsrCallback(RecognitionCallback):
    """SDK 回调：句子结束的识别文本桥接到 asyncio。"""

    def __init__(self, loop: asyncio.AbstractEventLoop, on_sentence) -> None:
        self._loop = loop
        self._on_sentence = on_sentence  # async callable(text)

    def on_event(self, result) -> None:
        try:
            sentence = result.get_sentence()
        except Exception:
            sentence = None
        if not sentence:
            return
        try:
            is_end = result.is_sentence_end(sentence)
        except Exception:
            is_end = True
        text = (sentence.get("text") or "").strip() if isinstance(sentence, dict) else ""
        if is_end and text and self._on_sentence is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._on_sentence(text), self._loop)
            except RuntimeError:
                logger.warning("事件循环已关闭，丢弃识别结果: %s", text[:20])

    def on_error(self, result) -> None:
        logger.warning(
            "ASR 识别错误: code=%s message=%s",
            getattr(result, "code", None),
            getattr(result, "message", None),
        )


class DashScopeASR:
    """每通语音通话一个实例：start 后持续 send PCM，句子结束回调识别文本。"""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_sentence,
        sample_rate: int | None = None,
        model: str | None = None,
    ) -> None:
        self._loop = loop
        dashscope.api_key = config.DASHSCOPE_API_KEY
        self._rec = Recognition(
            model=model or config.ASR_MODEL,
            callback=_AsrCallback(loop, on_sentence),
            format="pcm",
            sample_rate=sample_rate or config.ASR_SAMPLE_RATE,
            language_hints=["zh"],
        )
        self._started = False

    def start(self) -> bool:
        """启动识别。失败返回 False（不阻断通话，仅提示）。"""
        if not config.DASHSCOPE_API_KEY:
            logger.warning("未配置 DASHSCOPE_API_KEY，语音识别不可用")
            return False
        try:
            self._rec.start()
            self._started = True
            return True
        except Exception:
            logger.exception("ASR 启动失败")
            return False

    def send(self, data: bytes) -> None:
        """推送一段 PCM 音频（线程安全，内部入队）。"""
        if self._started and data:
            try:
                self._rec.send_audio_frame(data)
            except Exception:
                logger.exception("ASR 发送音频失败")

    def stop(self) -> None:
        """停止识别并释放连接。"""
        if not self._started:
            return
        try:
            self._rec.stop()
        except Exception:
            logger.exception("ASR 停止失败")
        finally:
            self._started = False

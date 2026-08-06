"""语音通话服务测试：模式切换 + WebSocket 流式回复（mock LLM，不触网）。"""

import asyncio
import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app.agent.coach import InterviewSession
from app.voice_server import _cosyvoice_synthesize, _synthesize, app, maybe_switch_to_mock
from fastapi.testclient import TestClient


async def _fake_synth(ws, state, sentence):
    sid = state["sid"] + 1
    state["sid"] = sid
    await ws.send_text(
        json.dumps({"type": "audio_start", "sid": sid, "text": sentence}, ensure_ascii=False)
    )
    await ws.send_text(json.dumps({"type": "audio_end", "sid": sid}))
    return True


def _recv_until_done(ws):
    """接收消息直到 'done'，返回 (deltas, audio_started, audio_ended, tts_errors)。"""
    deltas: list[str] = []
    audio_started = audio_ended = 0
    tts_errors = 0
    while True:
        msg = json.loads(ws.receive_text())
        t = msg["type"]
        if t == "delta":
            deltas.append(msg["content"])
        elif t == "audio_start":
            audio_started += 1
        elif t == "audio_end":
            audio_ended += 1
        elif t == "tts_error":
            tts_errors += 1
        elif t == "done":
            break
    return deltas, audio_started, audio_ended, tts_errors


#: 足够长、会被多句合并逻辑拆成两段（>TTS_FIRST_CHARS 触发首段）的回复文本
LONG_SPLIT_TEXT = (
    "第一句，这是用于测试合成中途失败的长句子内容，字数要足够多。"
    "第二句，这也是用于测试的较长句子内容。"
    "第三句继续。第四句继续。"
)


class VoiceServerTests(unittest.TestCase):
    def setUp(self):
        # 熔断状态是模块级全局，测试间必须重置，避免串扰
        import app.voice_server as voice_server

        voice_server._tts_circuit.update(fails=0, open_until=0.0)

    def test_maybe_switch_to_mock_on_first_message(self):
        s = InterviewSession("coach")
        s2 = maybe_switch_to_mock(s, "我想开始面试")
        self.assertEqual(s2.mode, "mock")
        # 非首条消息不切换
        s = InterviewSession("coach")
        s.messages.append({"role": "user", "content": "之前问过"})
        self.assertEqual(maybe_switch_to_mock(s, "开始面试").mode, "coach")

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter(["标准", "答案"]))
    def test_websocket_streams_reply(self, mock_chat_stream, mock_fts):
        with (
            TestClient(app) as client,
            mock.patch("app.voice_server._synthesize", side_effect=_fake_synth),
            client.websocket_connect("/ws/voice") as ws,
        ):
            # 接通后先收到开场白（像打电话一样），消化完再提问
            greeting_deltas, *_ = _recv_until_done(ws)
            ws.send_text(
                json.dumps({"type": "text", "content": "Redis 怎么答"}, ensure_ascii=False)
            )
            deltas, audio_started, audio_ended, _ = _recv_until_done(ws)
        self.assertTrue("".join(greeting_deltas).strip())
        self.assertEqual("".join(deltas), "标准答案")
        self.assertTrue(audio_started and audio_ended, "应推送音频开始/结束帧")

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter([LONG_SPLIT_TEXT]))
    def test_tts_degrade_after_first_failure(self, mock_chat_stream, mock_fts):
        """第一段在线合成失败后，后续段落直接降级为本地语音（tts_error），不再反复尝试。"""

        async def fail_once(ws, state, sentence):
            sid = state["sid"] + 1
            state["sid"] = sid
            await ws.send_text(
                json.dumps(
                    {"type": "audio_start", "sid": sid, "text": sentence}, ensure_ascii=False
                )
            )
            await ws.send_text(json.dumps({"type": "tts_error", "sid": sid}))
            return False

        with (
            TestClient(app) as client,
            mock.patch("app.voice_server._synthesize", side_effect=fail_once),
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白（同样走降级，不计数）
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            _, audio_starts, _, tts_errors = _recv_until_done(ws)
        self.assertEqual(tts_errors, 2)  # 两句话都降级
        self.assertEqual(audio_starts, 2)  # 降级路径每句都先发 audio_start（供浏览器回退对应文本）

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter(["流式回复。"]))
    def test_synthesize_streams_audio_chunks(self, mock_chat_stream, mock_fts):
        """edge-tts 小音频块被聚合成 ~8KB 大单元推送：单元数少、字节不丢、sid 有序。"""
        CHUNK = b"X" * 3000

        class FakeComm:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self):
                for _ in range(5):
                    yield {"type": "audio", "data": CHUNK}

        with (
            mock.patch("app.voice_server.edge_tts", SimpleNamespace(Communicate=FakeComm)),
            mock.patch("app.voice_server.config.VOICE_TTS", "edge"),
            TestClient(app) as client,
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白
            first_sid = None
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            sids: list[int] = []
            starts = ends = 0
            total_bytes = 0
            while True:
                m = json.loads(ws.receive_text())
                t = m["type"]
                if t == "reply_start":
                    first_sid = m["first_sid"]
                elif t == "audio_start":
                    starts += 1
                    sids.append(m["sid"])
                elif t == "audio":
                    total_bytes += len(base64.b64decode(m["data"]))
                elif t == "audio_end":
                    ends += 1
                elif t == "done":
                    break
        self.assertEqual(starts, 2)  # 5×3000B 聚合成 2 个单元（9000B + 6000B）
        self.assertEqual(ends, 2)
        self.assertEqual(total_bytes, 5 * 3000, "聚合不能丢字节")
        self.assertEqual(len(set(sids)), 2, "每个音频单元 sid 必须唯一")
        self.assertEqual(sorted(sids), list(range(first_sid, first_sid + 2)))

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter([LONG_SPLIT_TEXT]))
    def test_synthesize_midstream_failure_no_replay(self, mock_chat_stream, mock_fts):
        """合成中途失败：只推送已合成的部分，不整句重播；后续句子降级本地语音。"""

        class FakeCommFail:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self):
                yield {"type": "audio", "data": b"X" * 3000}
                raise ConnectionError("boom")

        with (
            mock.patch("app.voice_server.edge_tts", SimpleNamespace(Communicate=FakeCommFail)),
            mock.patch("app.voice_server.config.VOICE_TTS", "edge"),
            # 本测试只关心"中途失败不重播"，禁用熔断避免开场白失败提前打开熔断
            mock.patch("app.voice_server._tts_circuit_open", return_value=False),
            TestClient(app) as client,
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白（同样中途失败，先清空）
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            starts = ends = tts_errors = total_bytes = 0
            while True:
                m = json.loads(ws.receive_text())
                t = m["type"]
                if t == "audio_start":
                    starts += 1
                elif t == "audio":
                    total_bytes += len(base64.b64decode(m["data"]))
                elif t == "audio_end":
                    ends += 1
                elif t == "tts_error":
                    tts_errors += 1
                elif t == "done":
                    break
        self.assertEqual(starts, 2)  # 第一段部分音频 + 第二段降级
        self.assertEqual(ends, 1)  # 只有第一句推送了实际音频
        self.assertEqual(total_bytes, 3000)  # 只推已合成的部分，不整句重播
        self.assertEqual(tts_errors, 1)  # 第二句走降级

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch(
        "app.agent.llm.chat_stream", return_value=iter(["第一句。第二句。第三句。第四句。"])
    )
    def test_produce_merges_sentences_into_fewer_tts_calls(self, mock_chat_stream, mock_fts):
        """多句合并合成：短回复只发起 1 次在线合成（减少连接数、保留句间语气）。"""
        calls: list[str] = []

        async def fake_synth(ws, state, sentence):
            calls.append(sentence)
            return True

        with (
            mock.patch("app.voice_server._synthesize", side_effect=fake_synth),
            TestClient(app) as client,
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白
            calls.clear()
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            while True:
                if json.loads(ws.receive_text())["type"] == "done":
                    break
        self.assertEqual(len(calls), 1, "四句话应合并为一次合成")
        self.assertIn("第一句", calls[0])
        self.assertIn("第四句", calls[0])

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch(
        "app.agent.llm.chat_stream", return_value=iter(["第一句。第二句。第三句。第四句。"])
    )
    def test_produce_splits_long_text_into_chunks(self, mock_chat_stream, mock_fts):
        """长回复按阈值分批合成（阈值调小模拟长文本），句子不丢失。"""
        calls: list[str] = []

        async def fake_synth(ws, state, sentence):
            calls.append(sentence)
            return True

        with (
            mock.patch("app.voice_server._synthesize", side_effect=fake_synth),
            mock.patch("app.voice_server.TTS_FIRST_CHARS", 1),
            mock.patch("app.voice_server.TTS_CHUNK_CHARS", 1),
            TestClient(app) as client,
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白
            calls.clear()
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            while True:
                if json.loads(ws.receive_text())["type"] == "done":
                    break
        self.assertEqual(len(calls), 4, "四句话应各自成块")
        joined = "".join(calls)
        for s in ("第一句。", "第二句。", "第三句。", "第四句。"):
            self.assertIn(s, joined)

    def test_circuit_breaker_skips_online(self):
        """熔断期间 _synthesize 直接降级，不再调用 edge-tts（避免每次干等）。"""

        class FakeWS:
            def __init__(self):
                self.msgs = []

            async def send_text(self, s):
                self.msgs.append(json.loads(s))

        called = []

        class FakeComm:
            def __init__(self, *args, **kwargs):
                called.append(1)

            async def stream(self):
                yield {"type": "audio", "data": b"X"}

        async def run():
            with (
                mock.patch(
                    "app.voice_server._tts_circuit",
                    {"fails": 3, "open_until": time.monotonic() + 60},
                ),
                mock.patch("app.voice_server.edge_tts", SimpleNamespace(Communicate=FakeComm)),
                mock.patch("app.voice_server.config.VOICE_TTS", "edge"),
            ):
                ws = FakeWS()
                ok = await _synthesize(ws, {"sid": 0}, "你好")
            return ok, ws.msgs, called

        ok, msgs, called = asyncio.run(run())
        self.assertFalse(ok)
        self.assertEqual([m["type"] for m in msgs], ["audio_start", "tts_error"])
        self.assertEqual(called, [], "熔断期间不应发起在线合成")

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter(["CosyVoice 测试。"]))
    def test_cosyvoice_synthesize_sends_unit(self, mock_chat_stream, mock_fts):
        """VOICE_TTS=cosyvoice：整段音频作为一个播放单元推送。"""
        FAKE_AUDIO = b"\x00\x01\x02fake-cosy-audio"
        with (
            mock.patch("app.voice_server.config.VOICE_TTS", "cosyvoice"),
            mock.patch("app.voice_server.config.DASHSCOPE_API_KEY", "sk-test"),
            mock.patch("app.voice_server._cosyvoice_synthesize", return_value=FAKE_AUDIO),
            TestClient(app) as client,
            client.websocket_connect("/ws/voice") as ws,
        ):
            _recv_until_done(ws)  # 消化开场白（同样走 CosyVoice 路径）
            ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
            starts = ends = tts_errors = 0
            audio_bytes = b""
            while True:
                m = json.loads(ws.receive_text())
                t = m["type"]
                if t == "audio_start":
                    starts += 1
                elif t == "audio":
                    audio_bytes += base64.b64decode(m["data"])
                elif t == "audio_end":
                    ends += 1
                elif t == "tts_error":
                    tts_errors += 1
                elif t == "done":
                    break
        self.assertEqual(starts, 1)
        self.assertEqual(ends, 1)
        self.assertEqual(audio_bytes, FAKE_AUDIO)
        self.assertEqual(tts_errors, 0)

    def test_cosyvoice_retry_on_first_failure(self):
        """CosyVoice 首次请求失败（无音频）会重试一次，重试成功则正常推送。"""

        class FakeWS:
            def __init__(self):
                self.msgs = []

            async def send_text(self, s):
                self.msgs.append(json.loads(s))

        calls = []

        async def flaky(text):
            calls.append(text)
            return None if len(calls) == 1 else b"OK"

        ws = FakeWS()
        with (
            mock.patch("app.voice_server.config.VOICE_TTS", "cosyvoice"),
            mock.patch("app.voice_server.config.DASHSCOPE_API_KEY", "sk-test"),
            mock.patch("app.voice_server._cosyvoice_synthesize", side_effect=flaky),
        ):
            ok = asyncio.run(_synthesize(ws, {"sid": 0}, "你好"))
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2, "首次失败应重试一次")
        self.assertEqual([m["type"] for m in ws.msgs], ["audio_start", "audio", "audio_end"])

    def test_cosyvoice_no_key_fast_fail(self):
        """VOICE_TTS=cosyvoice 但缺少 DASHSCOPE_API_KEY：直接降级，不发网络请求。"""

        class FakeWS:
            def __init__(self):
                self.msgs = []

            async def send_text(self, s):
                self.msgs.append(json.loads(s))

        called = []

        async def never_called(text):
            called.append(text)
            return b"X"

        ws = FakeWS()
        with (
            mock.patch("app.voice_server.config.VOICE_TTS", "cosyvoice"),
            mock.patch("app.voice_server.config.DASHSCOPE_API_KEY", ""),
            mock.patch("app.voice_server._cosyvoice_synthesize", side_effect=never_called),
        ):
            ok = asyncio.run(_synthesize(ws, {"sid": 0}, "你好"))
        self.assertFalse(ok)
        self.assertEqual(called, [], "缺少 Key 时不应发起请求")
        self.assertEqual([m["type"] for m in ws.msgs], ["audio_start", "tts_error"])

    def test_cosyvoice_request_downloads_url(self):
        """非流式 CosyVoice 响应返回音频 URL，需二次下载后返回字节。"""

        class FakeResp:
            def __init__(self, json_data=None, content=None):
                self._json = json_data
                self.content = content

            def raise_for_status(self):
                pass

            def json(self):
                return self._json

        with (
            mock.patch("app.voice_server.config.DASHSCOPE_API_KEY", "sk-test"),
            mock.patch(
                "app.voice_server.requests.post",
                return_value=FakeResp(
                    json_data={"output": {"audio": {"url": "http://audio.example/x.mp3"}}}
                ),
            ),
            mock.patch("app.voice_server.requests.get", return_value=FakeResp(content=b"MP3DATA")),
        ):
            data = asyncio.run(_cosyvoice_synthesize("你好"))
        self.assertEqual(data, b"MP3DATA")

    def test_cosyvoice_request_accepts_base64_data(self):
        """若响应直接带 base64 音频（流式/内联），无需二次下载。"""

        class FakeResp:
            def __init__(self, json_data=None):
                self._json = json_data
                self.content = None

            def raise_for_status(self):
                pass

            def json(self):
                return self._json

        with (
            mock.patch("app.voice_server.config.DASHSCOPE_API_KEY", "sk-test"),
            mock.patch(
                "app.voice_server.requests.post",
                return_value=FakeResp(
                    json_data={
                        "output": {
                            "audio": {
                                "data": base64.b64encode(b"INLINE").decode("ascii"),
                                "url": "",
                            }
                        }
                    }
                ),
            ),
            mock.patch("app.voice_server.requests.get", side_effect=AssertionError("不应下载")),
        ):
            data = asyncio.run(_cosyvoice_synthesize("你好"))
        self.assertEqual(data, b"INLINE")

    def test_health(self):
        with TestClient(app) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_voice_page_served(self):
        """方案A：独立语音通话页由 FastAPI 直接托管，占位符被替换。"""
        with TestClient(app) as client:
            resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("callBtn", resp.text)
        self.assertNotIn("__VAD_THRESHOLD__", resp.text)
        self.assertNotIn("__WEB_URL__", resp.text)


if __name__ == "__main__":
    unittest.main()

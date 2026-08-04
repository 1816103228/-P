"""语音通话服务测试：模式切换 + WebSocket 流式回复（mock LLM，不触网）。"""
import json
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.agent.coach import InterviewSession
from voice_server import app, maybe_switch_to_mock


async def _fake_synth(ws, sid, sentence):
    await ws.send_text(json.dumps({"type": "audio_start", "sid": sid, "text": sentence}, ensure_ascii=False))
    await ws.send_text(json.dumps({"type": "audio_end", "sid": sid}))
    return True


class VoiceServerTests(unittest.TestCase):
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
        with TestClient(app) as client:
            with mock.patch("voice_server._synthesize", side_effect=_fake_synth):
                with client.websocket_connect("/ws/voice") as ws:
                    ws.send_text(json.dumps({"type": "text", "content": "Redis 怎么答"}, ensure_ascii=False))
                    deltas: list[str] = []
                    audio_started = audio_ended = False
                    while True:
                        msg = json.loads(ws.receive_text())
                        if msg["type"] == "delta":
                            deltas.append(msg["content"])
                        elif msg["type"] == "audio_start":
                            audio_started = True
                        elif msg["type"] == "audio_end":
                            audio_ended = True
                        elif msg["type"] == "done":
                            break
        self.assertEqual("".join(deltas), "标准答案")
        self.assertTrue(audio_started and audio_ended, "应推送音频开始/结束帧")

    @mock.patch("app.agent.coach.db.fts_search", return_value=[])
    @mock.patch("app.agent.llm.chat_stream", return_value=iter(["第一句。", "第二句。"]))
    def test_tts_degrade_after_first_failure(self, mock_chat_stream, mock_fts):
        """第一次在线合成失败后，后续句子直接降级为本地语音（tts_error），不再反复尝试。"""
        async def fail_once(ws, sid, sentence):
            await ws.send_text(json.dumps({"type": "tts_error", "sid": sid}))
            return False

        with TestClient(app) as client:
            with mock.patch("voice_server._synthesize", side_effect=fail_once):
                with client.websocket_connect("/ws/voice") as ws:
                    ws.send_text(json.dumps({"type": "text", "content": "你好"}, ensure_ascii=False))
                    tts_errors = 0
                    audio_starts = 0
                    while True:
                        msg = json.loads(ws.receive_text())
                        if msg["type"] == "tts_error":
                            tts_errors += 1
                        if msg["type"] == "audio_start":
                            audio_starts += 1
                        if msg["type"] == "done":
                            break
        self.assertEqual(tts_errors, 2)  # 两句话都降级
        self.assertEqual(audio_starts, 1)  # 只有第二句走降级路径（第一句在 _synthesize 内部失败）

    def test_health(self):
        with TestClient(app) as client:
            resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

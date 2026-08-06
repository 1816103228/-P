"""LLM 层测试：重试、失败熔断、流式输出（全部 mock，不触网）。"""

import unittest
from unittest import mock

from app import config
from app.agent import llm


def _fake_completion(text: str = "答案"):
    msg = mock.Mock()
    msg.content = text
    choice = mock.Mock()
    choice.message = msg
    resp = mock.Mock()
    resp.choices = [choice]
    resp.usage = None
    return resp


def _fake_chunk(text: str):
    chunk = mock.Mock()
    chunk.choices = [mock.Mock()]
    chunk.choices[0].delta.content = text
    return chunk


class LLMTests(unittest.TestCase):
    def test_is_api_key_configured(self):
        with mock.patch.object(config, "DEEPSEEK_API_KEY", "sk-你的key"):
            self.assertFalse(llm.is_api_key_configured())
        with mock.patch.object(config, "DEEPSEEK_API_KEY", "sk-短"):
            self.assertFalse(llm.is_api_key_configured())
        with mock.patch.object(config, "DEEPSEEK_API_KEY", "sk-" + "0" * 32):
            self.assertTrue(llm.is_api_key_configured())

    @mock.patch("app.agent.llm.time.sleep")
    def test_chat_retries_then_succeeds(self, mock_sleep):
        client = mock.Mock()
        client.chat.completions.create.side_effect = [ConnectionError("boom"), _fake_completion()]
        with mock.patch.object(llm, "get_client", return_value=client):
            text = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(text, "答案")
        self.assertEqual(client.chat.completions.create.call_count, 2)

    @mock.patch("app.agent.llm.time.sleep")
    def test_chat_retries_exhausted(self, mock_sleep):
        client = mock.Mock()
        client.chat.completions.create.side_effect = ConnectionError("boom")
        with (
            mock.patch.object(llm, "get_client", return_value=client),
            self.assertRaises(ConnectionError),
        ):
            llm.chat([{"role": "user", "content": "hi"}], max_retries=2)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_chat_stream_yields_deltas(self):
        client = mock.Mock()
        client.chat.completions.create.return_value = iter([_fake_chunk("你"), _fake_chunk("好")])
        with mock.patch.object(llm, "get_client", return_value=client):
            text = "".join(llm.chat_stream([{"role": "user", "content": "hi"}]))
        self.assertEqual(text, "你好")
        self.assertTrue(client.chat.completions.create.call_args.kwargs["stream"])


if __name__ == "__main__":
    unittest.main()

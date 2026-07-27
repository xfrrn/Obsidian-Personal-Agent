"""Token estimates and server-reported usage."""

from __future__ import annotations

import unittest

from agent.core.token_counter import TokenCounter
from agent.llm.session import _parse_usage
from agent.llm.types import TokenUsage


class TokenCounterTest(unittest.TestCase):
    def test_records_server_usage_and_never_relaxes_calibration(self) -> None:
        messages = [{"role": "user", "content": "请分析这个 Python 文件。"}]
        counter = TokenCounter("gpt-4.1-mini")
        initial_estimate = counter.estimate_request(messages, [])

        counter.record_response(messages, [], TokenUsage(100, 20, 120))

        self.assertEqual(counter.last_usage, TokenUsage(100, 20, 120))
        self.assertEqual(counter.total_prompt_tokens, 100)
        self.assertEqual(counter.total_completion_tokens, 20)
        self.assertEqual(counter.total_tokens, 120)
        self.assertGreaterEqual(counter.estimate_request(messages, []), initial_estimate)

    def test_parses_complete_usage_and_ignores_invalid_compatibility_payloads(self) -> None:
        self.assertEqual(_parse_usage({"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}), TokenUsage(10, 3, 13))
        self.assertIsNone(_parse_usage({"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 12}))
        self.assertIsNone(_parse_usage({"prompt_tokens": True, "completion_tokens": 3, "total_tokens": 4}))


if __name__ == "__main__":
    unittest.main()

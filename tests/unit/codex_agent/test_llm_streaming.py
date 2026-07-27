"""Model HTTP streaming and cancellation checks."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from agent.config.settings import Settings
from agent.llm.session import ModelClientSession, _http_error
from agent.llm.types import ContextLimitError, TokenUsage, ToolCall


class ModelStreamingTest(unittest.IsolatedAsyncioTestCase):
    def test_context_limit_http_error_is_typed_for_retry(self) -> None:
        error = _http_error(
            400,
            '{"error":{"code":"context_length_exceeded","message":"too long"}}',
        )

        self.assertIsInstance(error, ContextLimitError)

    def test_payload_uses_configured_temperature(self) -> None:
        self.assertEqual(_client("http://unused")._payload([], [])["temperature"], 0.0)

    async def test_streams_deltas_and_final_usage(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _read_request(reader)
            chunks = [
                {"choices": [{"delta": {"content": "你"}}]},
                {"choices": [{"delta": {"content": "好"}}]},
                {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11}},
            ]
            await _send_sse(writer, *(json.dumps(chunk, ensure_ascii=False) for chunk in chunks), "[DONE]")

        server, base_url = await _start_server(handler)
        try:
            received: list[str] = []

            async def record(delta: str) -> None:
                received.append(delta)

            response = await _client(base_url).stream_complete(
                [{"role": "user", "content": "test"}], [], record
            )
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(received, ["你", "好"])
        self.assertEqual(response.content, "你好")
        self.assertEqual(response.usage, TokenUsage(9, 2, 11))

    async def test_merges_fragmented_stream_tool_call(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _read_request(reader)
            chunks = [
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "call-1",
                    "function": {"name": "echo", "arguments": '{"text":"'},
                }]}}]},
                {"choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "function": {"arguments": 'hello"}'},
                }]}}]},
            ]
            await _send_sse(writer, *(json.dumps(chunk) for chunk in chunks), "[DONE]")

        server, base_url = await _start_server(handler)
        try:
            response = await _client(base_url).stream_complete(
                [{"role": "user", "content": "test"}], [], _ignore
            )
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(response.content, None)
        self.assertEqual(response.tool_calls, (ToolCall("call-1", "echo", {"text": "hello"}),))

    async def test_collects_reasoning_content(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _read_request(reader)
            chunks = [
                {"choices": [{"delta": {"reasoning_content": "检查"}}]},
                {"choices": [{"delta": {"reasoning": "目录"}}]},
            ]
            await _send_sse(writer, *(json.dumps(chunk, ensure_ascii=False) for chunk in chunks), "[DONE]")

        server, base_url = await _start_server(handler)
        try:
            response = await _client(base_url).stream_complete(
                [{"role": "user", "content": "test"}], [], _ignore
            )
        finally:
            server.close()
            await server.wait_closed()

        self.assertEqual(response.reasoning, "检查目录")

    async def test_preserves_optional_end_turn_signal(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _read_request(reader)
            await _send_sse(writer, json.dumps({"choices": [], "end_turn": False}), "[DONE]")

        server, base_url = await _start_server(handler)
        try:
            response = await _client(base_url).stream_complete(
                [{"role": "user", "content": "test"}], [], _ignore
            )
        finally:
            server.close()
            await server.wait_closed()

        self.assertIs(response.end_turn, False)

    async def test_cancellation_closes_the_stream_connection(self) -> None:
        first_delta_sent = asyncio.Event()
        peer_closed = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await _read_request(reader)
            await _send_sse(writer, json.dumps({"choices": [{"delta": {"content": "x"}}]}), close=False)
            first_delta_sent.set()
            await reader.read()
            peer_closed.set()
            writer.close()
            await writer.wait_closed()

        server, base_url = await _start_server(handler)
        try:
            async def ignore(_: str) -> None:
                return None

            request = asyncio.create_task(
                _client(base_url).stream_complete([{"role": "user", "content": "test"}], [], ignore)
            )
            await asyncio.wait_for(first_delta_sent.wait(), timeout=3)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            await asyncio.wait_for(peer_closed.wait(), timeout=3)
        finally:
            server.close()
            await server.wait_closed()


def _client(base_url: str) -> ModelClientSession:
    settings = Settings(
        api_key="test-key",
        model="test-model",
        base_url=base_url,
        system_prompt="test",
        workspace=Path.cwd(),
        shell_enabled=False,
        request_timeout_seconds=5,
        max_tool_rounds=1,
    )
    return ModelClientSession(settings)


async def _start_server(handler: object) -> tuple[asyncio.AbstractServer, str]:
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}/v1"


async def _read_request(reader: asyncio.StreamReader) -> None:
    headers = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
    content_length = next(
        int(line.split(":", 1)[1].strip()) for line in headers.split("\r\n") if line.lower().startswith("content-length:")
    )
    await reader.readexactly(content_length)


async def _send_sse(writer: asyncio.StreamWriter, *chunks: str, close: bool = True) -> None:
    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
    for chunk in chunks:
        writer.write(f"data: {chunk}\n\n".encode("utf-8"))
    await writer.drain()
    if close:
        writer.close()
        await writer.wait_closed()


async def _ignore(_: str) -> None:
    return None


if __name__ == "__main__":
    unittest.main()

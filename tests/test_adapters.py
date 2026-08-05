"""
适配器层测试
"""

from __future__ import annotations

import pytest

from webui_llm_proxy.adapters.models import ChatRequest, ChatResponse, MediaFile
from webui_llm_proxy.adapters.openai import (
    FunctionCallPromptBuilder,
    OpenAIRequestAdapter,
    OpenAIResponseAdapter,
    ToolCallParser,
)


class TestOpenAIRequestAdapter:
    """测试 OpenAI 请求适配器"""

    def test_parse_simple_request(self):
        body = {
            "model": "kimi-k2.6-fast",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
            "temperature": 0.5,
        }
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(body)

        assert isinstance(req, ChatRequest)
        assert req.model == "kimi-k2.6-fast"
        assert req.last_user_message == "Hello"
        assert req.stream is False
        assert req.temperature == 0.5
        assert req.has_images is False

    def test_parse_multimodal_request(self, openai_multimodal_request_body):
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(openai_multimodal_request_body)

        assert req.last_user_message == "Describe this image"
        assert req.has_images is True
        assert len(req.image_urls) == 1
        assert req.image_urls[0] == "https://example.com/image.png"

    def test_parse_request_defaults(self):
        body = {"messages": [{"role": "user", "content": "Test"}]}
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(body)

        assert req.stream is False
        assert req.temperature == 0.7
        assert req.max_tokens is None

    def test_parse_request_system_and_user_merged(self):
        body = {
            "model": "kimi-k2.6-fast",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"},
            ],
        }
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(body)

        assert req.last_user_message == "You are a helpful assistant.\n\nHello"

    def test_parse_request_with_tools(self):
        body = {
            "model": "kimi-k2.6-fast",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What's the weather?"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "location": {"type": "string", "description": "City name"},
                            },
                            "required": ["location"],
                        },
                    },
                }
            ],
        }
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(body)

        assert req.tools == body["tools"]
        assert req.tool_choice == "auto"
        assert "get_weather" in req.last_user_message
        assert "You are a helpful assistant." in req.last_user_message
        assert "What's the weather?" in req.last_user_message
        assert "location: string(required)" in req.last_user_message

    def test_parse_request_with_tool_results(self):
        body = {
            "model": "kimi-k2.6-fast",
            "messages": [
                {"role": "user", "content": "What's the weather?"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "get_weather", "arguments": '{"location": "Beijing"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_abc", "name": "get_weather", "content": "Sunny"},
                {"role": "user", "content": "Thanks"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "get_weather", "description": "Get weather", "parameters": {"type": "object"}},
                }
            ],
        }
        adapter = OpenAIRequestAdapter()
        req = adapter.parse_request(body)

        assert "[Tool result for call_abc - get_weather]" in req.last_user_message
        assert "Sunny" in req.last_user_message
        assert "[Assistant called tools]" in req.last_user_message
        assert "call_abc" in req.last_user_message
        assert "Thanks" in req.last_user_message


class TestToolCallParser:
    """测试 function call 解析"""

    def test_parse_json_codeblock(self):
        text = 'Some analysis...\n```json\n{"tool_calls": [{"function": {"name": "get_weather", "arguments": {"location": "Beijing"}}}]}\n```'
        calls = ToolCallParser.parse(text)

        assert len(calls) == 1
        assert calls[0]["type"] == "function"
        assert calls[0]["function"]["name"] == "get_weather"
        assert '"location": "Beijing"' in calls[0]["function"]["arguments"]

    def test_parse_plain_json(self):
        text = '{"tool_calls": [{"function": {"name": "get_time", "arguments": {"timezone": "UTC"}}}]}'
        calls = ToolCallParser.parse(text)

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "get_time"

    def test_parse_xml(self):
        text = '<tool_calls>[{"function": {"name": "calc", "arguments": {"a": 1, "b": 2}}}]</tool_calls>'
        calls = ToolCallParser.parse(text)

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "calc"

    def test_parse_plain_json_with_prefix(self):
        # 模拟 Gemini 页面 text_content 带前缀的场景
        text = 'Gemini 说JSON{"tool_calls": [{"function": {"name": "read", "arguments": {"path": "./webui_llm_proxy/README.md"}}}]}'
        calls = ToolCallParser.parse(text)

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read"
        assert '"path": "./webui_llm_proxy/README.md"' in calls[0]["function"]["arguments"]

    def test_parse_array_with_prefix(self):
        text = 'Here is the result: [{"function": {"name": "read", "arguments": {"path": "x"}}}]'
        calls = ToolCallParser.parse(text)

        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "read"

    def test_parse_no_match(self):
        assert ToolCallParser.parse("Just a normal response.") == []
        assert ToolCallParser.parse("") == []


class TestFunctionCallPromptBuilder:
    """测试工具 prompt 构建"""

    def test_build_tool_prompt(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get current weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                },
            }
        ]
        prompt = FunctionCallPromptBuilder.build(tools)

        assert "get_weather" in prompt
        assert "location: string(required)" in prompt
        assert '```json' in prompt
        assert "tool_calls" in prompt

    def test_build_with_tool_choice_none(self):
        tools = [{"type": "function", "function": {"name": "foo", "description": "bar", "parameters": {"type": "object"}}}]
        prompt = FunctionCallPromptBuilder.build(tools, tool_choice="none")

        assert "当前不需要调用工具" in prompt


class TestOpenAIResponseAdapter:
    """测试 OpenAI 响应适配器"""

    def test_build_response(self):
        adapter = OpenAIResponseAdapter()
        response = ChatResponse(content="Hello world", model="kimi-k2.6-fast")
        result = adapter.build_response(response)

        assert result["object"] == "chat.completion"
        assert result["model"] == "kimi-k2.6-fast"
        assert result["choices"][0]["message"]["content"] == "Hello world"
        assert result["choices"][0]["finish_reason"] == "stop"
        assert "usage" in result

    def test_build_response_with_media(self):
        adapter = OpenAIResponseAdapter()
        media = MediaFile(
            filename="test.png",
            path="/media/test.png",
            local_path="./data/media/test.png",
            source="blob:test",
        )
        response = ChatResponse(
            content="Here is an image",
            model="kimi-k2.6-fast",
            media_files=[media],
        )
        result = adapter.build_response(response)

        assert "custom_content" in result["choices"][0]["message"]
        assert len(result["choices"][0]["message"]["custom_content"]["media_files"]) == 1

    def test_build_stream_chunk(self):
        adapter = OpenAIResponseAdapter()
        chunk = adapter.build_stream_chunk("Hello", model="kimi-k2.6-fast")

        assert chunk.startswith("data: ")
        assert "Hello" in chunk

    def test_build_stream_finish_chunk(self):
        adapter = OpenAIResponseAdapter()
        chunk = adapter.build_stream_chunk("", model="kimi-k2.6-fast", finish=True)

        assert '"finish_reason": "stop"' in chunk

    def test_build_response_with_tool_calls(self):
        adapter = OpenAIResponseAdapter()
        response = ChatResponse(
            content="",
            model="kimi-k2.6-fast",
            tool_calls=[
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"location": "Beijing"}'},
                }
            ],
        )
        result = adapter.build_response(response)

        assert result["choices"][0]["finish_reason"] == "tool_calls"
        assert result["choices"][0]["message"]["content"] is None
        assert result["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "get_weather"

    def test_build_stream_finish_chunk_with_tool_calls(self):
        adapter = OpenAIResponseAdapter()
        tool_calls = [
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"location": "Beijing"}'},
            }
        ]
        chunk = adapter.build_finish_chunk(model="kimi-k2.6-fast", tool_calls=tool_calls)

        assert '"finish_reason": "tool_calls"' in chunk
        assert "tool_calls" in chunk

    def test_build_stream_end(self):
        adapter = OpenAIResponseAdapter()
        end = adapter.build_stream_end()
        assert end == "data: [DONE]\n\n"

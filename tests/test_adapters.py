"""
适配器层测试
"""

from __future__ import annotations

import pytest

from webui_llm_proxy.adapters.models import ChatRequest, ChatResponse, MediaFile
from webui_llm_proxy.adapters.openai import OpenAIRequestAdapter, OpenAIResponseAdapter


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

    def test_build_stream_end(self):
        adapter = OpenAIResponseAdapter()
        end = adapter.build_stream_end()
        assert end == "data: [DONE]\n\n"

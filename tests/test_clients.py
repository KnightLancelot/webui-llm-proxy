"""
客户端层测试
"""

from __future__ import annotations

import asyncio
import os

import pytest

from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.clients.gemini import GeminiClient
from webui_llm_proxy.clients.kimi import KimiClient


class TestLLMClientFactory:
    """测试抽象工厂"""

    def test_factory_registers_clients(self):
        registered = LLMClientFactory.list_registered()
        assert "gemini" in registered
        assert "kimi" in registered
        assert "moonshot" in registered

    def test_factory_creates_kimi_client(self):
        client = LLMClientFactory.create("kimi-k2.6-fast")
        assert isinstance(client, KimiClient)

    def test_factory_creates_gemini_client_by_default(self):
        client = LLMClientFactory.create("unknown-model")
        assert isinstance(client, GeminiClient)

    def test_factory_creates_gemini_client_explicitly(self):
        client = LLMClientFactory.create("gemini-pro")
        assert isinstance(client, GeminiClient)

    def test_factory_is_case_insensitive(self):
        client = LLMClientFactory.create("KIMI-K2.6-FAST")
        assert isinstance(client, KimiClient)

    def test_factory_creates_new_instances(self):
        c1 = LLMClientFactory.create("kimi")
        c2 = LLMClientFactory.create("kimi")
        assert c1 is not c2


class TestGeminiClient:
    """测试 Gemini 客户端钩子方法"""

    def test_get_chat_url(self):
        client = GeminiClient()
        assert "gemini.google.com" in client._get_chat_url()

    def test_get_browser_profile(self):
        client = GeminiClient()
        assert "chrome" in client._get_browser_profile()

    def test_is_ready_before_start(self):
        client = GeminiClient()
        assert client.is_ready is False

    def test_prepare_long_message_short_prompt(self):
        client = GeminiClient()
        msg, paths = asyncio.run(client._prepare_long_message("short", ["/a.txt"]))
        assert msg == "short"
        assert paths == ["/a.txt"]

    def test_prepare_long_message_long_prompt(self, tmp_path, monkeypatch):
        from webui_llm_proxy.config import settings
        monkeypatch.setattr(settings.gemini, "long_message_threshold", 10)
        uploads = tmp_path / "uploads"
        monkeypatch.setattr(settings.upload, "temp_dir", str(uploads))

        client = GeminiClient()
        long_text = "a" * 50
        msg, paths = asyncio.run(client._prepare_long_message(long_text, ["/a.txt"]))
        assert len(paths) == 2
        assert paths[0] == "/a.txt"
        prompt_path = paths[1]
        assert prompt_path.endswith(".txt")
        assert os.path.exists(prompt_path)
        with open(prompt_path, encoding="utf-8") as f:
            assert f.read() == long_text
        assert "附件" in msg or "文件" in msg


class TestKimiClient:
    """测试 Kimi 客户端钩子方法"""

    def test_get_chat_url(self):
        client = KimiClient()
        assert "kimi.moonshot.cn" in client._get_chat_url()

    def test_get_browser_profile(self):
        client = KimiClient()
        assert "kimi" in client._get_browser_profile()

    def test_default_state(self):
        client = KimiClient()
        assert client.last_media_files == []
        assert client.has_undownloadable_files is False

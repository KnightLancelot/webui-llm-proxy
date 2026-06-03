"""
Pytest 共享配置和 fixtures
"""

from __future__ import annotations

import pytest

from webui_llm_proxy.adapters.models import ChatRequest
from webui_llm_proxy.core.event_bus import EventBus
from webui_llm_proxy.core.memory import MemoryManager


@pytest.fixture
def event_bus():
    """创建新的事件总线实例（每次测试隔离）"""
    return EventBus()


@pytest.fixture
def sample_chat_request() -> ChatRequest:
    """示例聊天请求"""
    return ChatRequest(
        messages=[{"role": "user", "content": "Hello"}],
        model="kimi-k2.6-fast",
        stream=False,
        temperature=0.7,
        last_user_message="Hello",
    )


@pytest.fixture
def openai_multimodal_request_body() -> dict:
    """OpenAI 多模态请求体示例"""
    return {
        "model": "kimi-k2.6-fast",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
                ],
            }
        ],
        "stream": False,
        "temperature": 0.7,
    }


@pytest.fixture(autouse=True)
def reset_memory_singleton():
    """每次测试前重置 MemoryManager 单例"""
    MemoryManager._instance = None
    MemoryManager._initialized = False
    yield
    MemoryManager._instance = None
    MemoryManager._initialized = False

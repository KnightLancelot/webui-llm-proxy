"""
核心模块测试
"""

from __future__ import annotations

import pytest

from webui_llm_proxy.core.event_bus import ChatEvent, EventBus, EventContext, EventObserver
from webui_llm_proxy.core.memory import MemoryManager


class TestEventBus:
    """测试观察者模式 — 事件总线"""

    @pytest.mark.asyncio
    async def test_subscribe_and_emit(self, event_bus):
        events_received = []

        class TestObserver(EventObserver):
            async def on_event(self, event, context):
                events_received.append((event, context.get("data")))

        observer = TestObserver()
        event_bus.subscribe(ChatEvent.RESPONSE_RECEIVED, observer)
        await event_bus.emit(ChatEvent.RESPONSE_RECEIVED, data="test_data")

        assert len(events_received) == 1
        assert events_received[0][0] == ChatEvent.RESPONSE_RECEIVED
        assert events_received[0][1] == "test_data"

    @pytest.mark.asyncio
    async def test_callback_subscription(self, event_bus):
        events = []
        event_bus.on(ChatEvent.MESSAGE_RECEIVED, lambda ctx: events.append(ctx.get("msg")))
        await event_bus.emit(ChatEvent.MESSAGE_RECEIVED, msg="hello")

        assert events == ["hello"]

    @pytest.mark.asyncio
    async def test_unsubscribe(self, event_bus):
        events = []

        class TestObserver(EventObserver):
            async def on_event(self, event, context):
                events.append(event)

        observer = TestObserver()
        event_bus.subscribe(ChatEvent.RESPONSE_RECEIVED, observer)
        event_bus.unsubscribe(ChatEvent.RESPONSE_RECEIVED, observer)
        await event_bus.emit(ChatEvent.RESPONSE_RECEIVED)

        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_observer_exception_handling(self, event_bus, caplog):
        class BadObserver(EventObserver):
            async def on_event(self, event, context):
                raise ValueError("test error")

        event_bus.subscribe(ChatEvent.RESPONSE_RECEIVED, BadObserver())
        await event_bus.emit(ChatEvent.RESPONSE_RECEIVED)

        assert "test error" in caplog.text


class TestMemoryManager:
    """测试单例模式 — 记忆管理器"""

    def test_singleton(self):
        m1 = MemoryManager()
        m2 = MemoryManager()
        assert m1 is m2

    def test_add_message(self):
        memory = MemoryManager()
        memory.add_message("user", "Hello")
        memory.add_message("assistant", "Hi there")

        assert len(memory.short_term) == 2
        assert memory.short_term[0]["role"] == "user"
        assert memory.short_term[1]["role"] == "assistant"

    def test_get_context(self):
        memory = MemoryManager()
        memory.add_message("user", "What is AI?")
        memory.add_message("assistant", "AI stands for Artificial Intelligence.")

        context = memory.get_context(max_rounds=5)
        assert "What is AI?" in context
        assert "Artificial Intelligence" in context

    def test_get_openai_messages(self):
        memory = MemoryManager()
        memory.add_message("user", "Q1")
        memory.add_message("assistant", "A1")

        messages = memory.get_openai_messages()
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "Q1"}
        assert messages[1] == {"role": "assistant", "content": "A1"}

    def test_clear_short_term(self):
        memory = MemoryManager()
        memory.add_message("user", "Hello")
        memory.clear_short_term()

        assert len(memory.short_term) == 0

    def test_get_status(self):
        memory = MemoryManager()
        memory.add_message("user", "Test")
        status = memory.get_status()

        assert status["short_term_count"] == 1
        assert status["long_term_count"] == 0

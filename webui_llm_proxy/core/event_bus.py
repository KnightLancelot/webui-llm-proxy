"""
事件总线 — 观察者模式 (Observer Pattern)

将记忆更新、使用日志、媒体文件提取等操作从核心流程中解耦，
通过事件订阅/发布机制实现模块化扩展。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum, auto
from typing import Callable

logger = logging.getLogger(__name__)


class ChatEvent(Enum):
    """聊天相关事件类型"""
    MESSAGE_RECEIVED = auto()      # 收到用户消息
    RESPONSE_RECEIVED = auto()     # 收到完整模型回复（非流式）
    STREAM_COMPLETED = auto()      # 流式响应完成
    MEDIA_EXTRACTED = auto()       # 媒体文件提取完成
    SESSION_CLEANED = auto()       # 会话清理完成


class EventContext:
    """事件上下文，携带事件相关数据"""
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


class EventObserver(ABC):
    """事件观察者抽象基类"""

    @abstractmethod
    async def on_event(self, event: ChatEvent, context: EventContext) -> None:
        """处理事件"""
        ...


class EventBus:
    """
    事件总线

    管理事件的订阅和发布，支持同步和异步观察者。
    """

    def __init__(self) -> None:
        self._observers: dict[ChatEvent, list[EventObserver]] = {}
        self._callbacks: dict[ChatEvent, list[Callable]] = {}

    def subscribe(self, event: ChatEvent, observer: EventObserver) -> None:
        """订阅事件（观察者对象）"""
        if event not in self._observers:
            self._observers[event] = []
        self._observers[event].append(observer)
        logger.debug(f"Observer {observer.__class__.__name__} subscribed to {event.name}")

    def unsubscribe(self, event: ChatEvent, observer: EventObserver) -> None:
        """取消订阅"""
        if event in self._observers:
            self._observers[event] = [o for o in self._observers[event] if o is not observer]

    def on(self, event: ChatEvent, callback: Callable[[EventContext], None]) -> None:
        """订阅事件（回调函数）"""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    async def emit(self, event: ChatEvent, **kwargs) -> None:
        """
        发布事件

        Args:
            event: 事件类型
            **kwargs: 事件上下文数据
        """
        context = EventContext(**kwargs)

        # 通知观察者对象
        for observer in self._observers.get(event, []):
            try:
                await observer.on_event(event, context)
            except Exception as e:
                logger.warning(f"Observer {observer.__class__.__name__} failed to process event {event.name}: {e}")

        # 通知回调函数
        for callback in self._callbacks.get(event, []):
            try:
                callback(context)
            except Exception as e:
                logger.warning(f"Callback failed to process event {event.name}: {e}")

    def get_subscriber_count(self, event: ChatEvent) -> int:
        """获取某事件的订阅者数量"""
        observers = len(self._observers.get(event, []))
        callbacks = len(self._callbacks.get(event, []))
        return observers + callbacks

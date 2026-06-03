"""
回复完成检测策略 — 策略模式 (Strategy Pattern)

将不同的回复完成判定逻辑抽象为可插拔策略，
客户端通过构造函数注入所需策略。
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webui_llm_proxy.clients.base import BaseLLMClient

logger = logging.getLogger(__name__)


class CompletionDetectionStrategy(ABC):
    """
    回复完成检测策略抽象基类
    """

    @abstractmethod
    async def is_complete(self, client: BaseLLMClient) -> bool:
        """
        判断当前回复是否已完成

        Args:
            client: LLM 客户端实例（用于访问 page、配置等）

        Returns:
            True 如果回复已完成，False 如果仍在生成中
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """重置策略内部状态（每次新请求前调用）"""
        ...


class StableCountStrategy(CompletionDetectionStrategy):
    """
    稳定计数策略 — 连续 N 次轮询文本不变认为完成
    适用于 Gemini 等回复逐字追加的场景
    """

    def __init__(self, threshold: int = 3, idle_timeout: float = 30.0) -> None:
        self._threshold = threshold
        self._idle_timeout = idle_timeout
        self._stable_count = 0
        self._last_text = ""
        self._idle_start = 0.0

    def reset(self) -> None:
        self._stable_count = 0
        self._last_text = ""
        self._idle_start = time.time()

    async def is_complete(self, client: BaseLLMClient) -> bool:
        current_text = await client._extract_response_text()

        if current_text == self._last_text:
            self._stable_count += 1
        else:
            self._stable_count = 0
            self._last_text = current_text
            self._idle_start = time.time()

        if self._stable_count >= self._threshold:
            logger.debug(f"Stable count reached threshold {self._threshold}, reply complete")
            return True

        if time.time() - self._idle_start > self._idle_timeout:
            logger.debug("Stream idle timeout, reply complete")
            return True

        return False


class DOMStateStrategy(CompletionDetectionStrategy):
    """
    DOM 状态检测策略 — 通过检测 DOM 元素判断回复完成
    适用于 Kimi 等有明确 action bar / loading 动画的页面
    """

    def __init__(self, poll_interval_ms: float = 1000.0) -> None:
        self._poll_interval_ms = poll_interval_ms

    def reset(self) -> None:
        pass

    async def is_complete(self, client: BaseLLMClient) -> bool:
        page = client._get_page()
        try:
            result = await page.evaluate(
                """() => {
                    const actionBar = document.querySelector('.segment-assistant-actions-content');
                    const lastNode = document.querySelector('.segment-content-box.last-node');
                    const hasActionBar = !!actionBar && actionBar.getBoundingClientRect().width > 0;
                    const hasLoading = !!lastNode && (
                        lastNode.querySelector('.loading, .spin, .spinner, [class*="loading"]') ||
                        lastNode.classList.contains('last-node')
                    );
                    return {
                        finished: hasActionBar,
                        generating: hasLoading && !hasActionBar,
                        has_action_bar: hasActionBar,
                        has_loading: hasLoading
                    };
                }"""
            )
            finished = result.get("finished", False)
            if finished:
                logger.debug("Action bar detected, reply complete")
            return finished
        except Exception as e:
            logger.warning(f"DOM state detection error: {e}")
            return False


class HybridStrategy(CompletionDetectionStrategy):
    """
    组合策略 — 先尝试 DOM 状态检测，超时后回退到稳定计数
    """

    def __init__(
        self,
        dom_strategy: CompletionDetectionStrategy | None = None,
        fallback_strategy: CompletionDetectionStrategy | None = None,
        fallback_timeout: float = 300.0,
    ) -> None:
        self._dom = dom_strategy or DOMStateStrategy()
        self._fallback = fallback_strategy or StableCountStrategy(threshold=3, idle_timeout=60.0)
        self._fallback_timeout = fallback_timeout
        self._start_time = 0.0
        self._use_fallback = False

    def reset(self) -> None:
        self._dom.reset()
        self._fallback.reset()
        self._start_time = time.time()
        self._use_fallback = False

    async def is_complete(self, client: BaseLLMClient) -> bool:
        if not self._use_fallback:
            result = await self._dom.is_complete(client)
            if result:
                return True
            if time.time() - self._start_time > self._fallback_timeout:
                logger.warning("DOM strategy timeout, switching to stable count strategy")
                self._use_fallback = True
                self._fallback.reset()
            return False

        return await self._fallback.is_complete(client)

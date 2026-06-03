"""
LLM 客户端工厂 — 抽象工厂模式 (Abstract Factory Pattern)

根据模型名称自动创建对应的 LLM 客户端实例。
子类通过注册机制自动加入工厂，无需修改工厂代码即可扩展新后端。
"""

from __future__ import annotations

import logging
from typing import Optional, Type

from webui_llm_proxy.browser.controller import BrowserController
from webui_llm_proxy.clients.base import BaseLLMClient

logger = logging.getLogger(__name__)


class LLMClientFactory:
    """
    LLM 客户端抽象工厂

    使用注册表模式管理客户端类型，根据模型名称前缀自动匹配并创建实例。
    """

    _registry: dict[str, Type[BaseLLMClient]] = {}

    @classmethod
    def register(cls, model_prefix: str, client_class: Type[BaseLLMClient]) -> None:
        """
        注册客户端类型

        Args:
            model_prefix: 模型名称前缀（如 'kimi', 'gemini'）
            client_class: 客户端类（必须继承 BaseLLMClient）
        """
        cls._registry[model_prefix.lower()] = client_class
        logger.debug(f"Registered client: {model_prefix} -> {client_class.__name__}")

    @classmethod
    def create(
        cls,
        model: str,
        browser: Optional[BrowserController] = None,
    ) -> BaseLLMClient:
        """
        根据模型名称创建对应的客户端实例

        Args:
            model: 模型名称（如 'kimi-k2.6-fast', 'gemini-pro'）
            browser: 浏览器控制器实例（可选，默认创建新实例）

        Returns:
            BaseLLMClient 子类实例
        """
        model_lower = (model or "").lower()

        for prefix, client_class in cls._registry.items():
            if prefix in model_lower:
                logger.info(f"Factory created client: model='{model}' -> {client_class.__name__}")
                return client_class(browser)

        # 默认返回 Gemini
        from webui_llm_proxy.clients.gemini import GeminiClient

        logger.info(f"Factory created default client: model='{model}' -> GeminiClient")
        return GeminiClient(browser)

    @classmethod
    def list_registered(cls) -> list[str]:
        """返回已注册的模型前缀列表"""
        return list(cls._registry.keys())

    @classmethod
    def get_model_prefix(cls, model: str) -> str | None:
        """根据模型名称提取已注册的前缀（长前缀优先匹配）"""
        model_lower = (model or "").lower()
        for prefix in sorted(cls._registry.keys(), key=lambda x: -len(x)):
            if prefix in model_lower:
                return prefix
        return None


# 自动导入所有客户端子模块，触发注册
try:
    import webui_llm_proxy.clients.gemini  # noqa: F401
    import webui_llm_proxy.clients.kimi    # noqa: F401
except ImportError:
    pass

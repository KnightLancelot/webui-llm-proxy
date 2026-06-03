"""
适配器抽象接口 — 适配器模式 (Adapter Pattern)

定义统一的请求解析和响应构建接口，
具体适配器（OpenAI、Claude 等）实现该接口以支持不同 API 格式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from webui_llm_proxy.adapters.models import ChatRequest, ChatResponse


class RequestAdapter(ABC):
    """请求适配器：将外部 API 请求解析为内部 ChatRequest"""

    @abstractmethod
    def parse_request(self, body: dict) -> ChatRequest:
        """解析请求体为内部格式"""
        ...


class ResponseAdapter(ABC):
    """响应适配器：将内部 ChatResponse 构建为外部 API 响应"""

    @abstractmethod
    def build_response(self, response: ChatResponse) -> dict:
        """构建非流式响应"""
        ...

    @abstractmethod
    def build_stream_chunk(
        self,
        delta: str,
        model: str,
        finish: bool = False,
        custom_content: dict | None = None,
    ) -> str:
        """构建流式 SSE 数据块"""
        ...

    @abstractmethod
    def build_stream_end(self) -> str:
        """构建 SSE 流结束标记"""
        ...

    @abstractmethod
    async def stream_response(
        self,
        text_stream: AsyncGenerator[str, None],
        model: str,
        custom_content: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        将内部文本流转换为外部格式的 SSE 流

        Args:
            text_stream: 内部文本片段生成器
            model: 模型名称
            custom_content: 自定义扩展内容（如媒体文件列表）

        Yields:
            SSE 格式的字符串
        """
        ...

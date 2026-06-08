"""
FastAPI 应用入口 — 依赖注入 (Dependency Injection)

使用 lifespan 上下文管理器管理全局资源生命周期，
通过 app.state 实现依赖注入，消除全局变量。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.clients.pool import ClientPool
from webui_llm_proxy.config import settings
from webui_llm_proxy.core.event_bus import ChatEvent, EventBus
from webui_llm_proxy.core.media_extractor import MediaExtractorObserver
from webui_llm_proxy.core.memory import MemoryManager
from webui_llm_proxy.core.usage_logger import UsageLogger

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """配置全局日志"""
    logging.basicConfig(
        level=getattr(logging, settings.log.level.upper(), logging.INFO),
        format=settings.log.format,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI  lifespan 上下文管理器

    启动时：初始化客户端池、注册观察者、创建全局状态
    关闭时：清理资源
    """
    setup_logging()
    logger.info("=" * 50)
    logger.info("Starting WebUI LLM Proxy...")
    logger.info("=" * 50)

    # 初始化全局资源
    event_bus = EventBus()
    memory = MemoryManager()

    # 按模型前缀创建客户端池（每个模型 N 个独立浏览器实例）
    client_pools: dict[str, ClientPool] = {}
    for prefix in settings.enabled_model_list:
        pool = ClientPool(prefix, settings.browser.pool_size)
        await pool.initialize()
        client_pools[prefix] = pool
        logger.info(f"Client pool ready for model: {prefix} (size={settings.browser.pool_size})")

    # 注册观察者
    event_bus.subscribe(ChatEvent.RESPONSE_RECEIVED, UsageLogger())
    event_bus.subscribe(ChatEvent.STREAM_COMPLETED, UsageLogger())
    event_bus.subscribe(ChatEvent.MEDIA_EXTRACTED, MediaExtractorObserver())

    # 存储到 app.state（依赖注入源）
    app.state.client_pools = client_pools
    app.state.event_bus = event_bus
    app.state.memory = memory
    app.state.enabled_models = settings.enabled_model_list

    logger.info(f"Service ready. Enabled models: {settings.enabled_model_list}")
    yield

    # 关闭时清理
    logger.info("Shutting down service...")
    for prefix, pool in client_pools.items():
        try:
            await pool.close()
            logger.info(f"Client pool closed for model: {prefix}")
        except Exception as e:
            logger.warning(f"Error closing client pool for {prefix}: {e}")
    logger.info("Service stopped")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    app = FastAPI(
        title="WebUI LLM Proxy",
        description="A design-pattern-refactored proxy server for Web UI LLMs",
        version="3.0.0",
        lifespan=lifespan,
    )

    # 静态文件服务（媒体文件）
    os.makedirs(settings.media_dir, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

    # 注册路由
    from webui_llm_proxy.api.routes import chat, upload, models, memory, debug

    app.include_router(chat.router)
    app.include_router(upload.router)
    app.include_router(models.router)
    app.include_router(memory.router)
    app.include_router(debug.router)

    @app.get("/")
    async def root():
        return {
            "message": "WebUI LLM Proxy is running",
            "docs": "/docs",
            "version": "3.0.0",
        }

    return app


# 依赖注入辅助函数

def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus


def get_memory(request: Request) -> MemoryManager:
    return request.app.state.memory

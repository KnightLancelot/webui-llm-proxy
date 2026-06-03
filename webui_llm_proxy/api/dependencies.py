"""
FastAPI 依赖注入 — 装饰器模式 (Decorator Pattern)

将 API Key 校验、编码修复、请求解析等功能封装为可复用的依赖项，
通过 FastAPI Depends 机制注入到路由处理函数中。
"""

from __future__ import annotations

import logging
import re

from fastapi import Depends, Form, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from webui_llm_proxy.adapters.models import ChatRequest
from webui_llm_proxy.adapters.openai import OpenAIRequestAdapter
from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.clients.pool import ClientPool
from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> bool:
    """
    API Key 校验依赖

    支持配置单个或多个 key（逗号分隔），未配置则跳过校验。
    """
    allowed_keys = settings.openai.api_keys
    if not allowed_keys:
        return True
    if not credentials or credentials.credentials not in allowed_keys:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return True


async def parse_chat_request(request: Request) -> ChatRequest:
    """
    解析 OpenAI 格式聊天请求

    从请求体中解析出 ChatRequest 数据类。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    adapter = OpenAIRequestAdapter()
    return adapter.parse_request(body)


def get_pool(request: Request, model: str) -> ClientPool:
    """
    根据模型名称获取对应的客户端池。
    """
    prefix = LLMClientFactory.get_model_prefix(model)
    if prefix is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model '{model}'. Registered prefixes: {LLMClientFactory.list_registered()}",
        )

    enabled_models: list[str] = request.app.state.enabled_models
    if prefix not in enabled_models:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' (prefix '{prefix}') is not enabled. "
                   f"Enabled models: {enabled_models}",
        )

    pools: dict[str, ClientPool] = request.app.state.client_pools
    pool = pools.get(prefix)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail=f"Client pool for '{prefix}' not initialized",
        )

    return pool


def fix_encoding(text: str) -> str:
    """
    尝试修复 Windows curl 发送的 multipart form-data 中文乱码。

    Windows curl 默认使用 GBK 编码发送非 ASCII 字符，
    但 FastAPI 的 Form() 默认按 UTF-8 / Latin-1 解码，导致乱码。
    """
    if not text:
        return text

    # 如果已经包含正常中文字符，直接返回
    if re.search(r"[\u4e00-\u9fff]", text):
        return text

    # 如果纯 ASCII，直接返回
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        pass

    # 方案 1: GBK 编码被错误地当作 Latin-1 解码
    try:
        fixed = text.encode("latin-1").decode("gbk")
        if re.search(r"[\u4e00-\u9fff]", fixed):
            logger.debug(f"Encoding fix (latin-1->gbk): {text[:30]}... -> {fixed[:30]}...")
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    # 方案 2: UTF-8 编码被错误地当作 Latin-1 解码
    try:
        fixed = text.encode("latin-1").decode("utf-8")
        if re.search(r"[\u4e00-\u9fff]", fixed):
            logger.debug(f"Encoding fix (latin-1->utf-8): {text[:30]}... -> {fixed[:30]}...")
            return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    # 方案 3: GBK 编码被错误地当作 UTF-8 解码
    if "\ufffd" in text:
        try:
            fixed = text.encode("utf-8", errors="ignore").decode("gbk", errors="ignore")
            if re.search(r"[\u4e00-\u9fff]", fixed):
                logger.debug(f"Encoding fix (utf-8->gbk, lossy): {fixed[:30]}...")
                return fixed
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass

    return text


async def parse_upload_request(
    message: str = Form(...),
    model: str = Form(default=""),
    stream: bool = Form(default=False),
) -> tuple[str, str, bool]:
    """
    解析文件上传请求参数

    Returns:
        (修复编码后的消息, 模型名称, 是否流式)
    """
    return fix_encoding(message), model, stream

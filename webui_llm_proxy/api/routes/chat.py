"""
聊天补全路由 — /v1/chat/completions

支持 stream=true（流式）和 stream=false（非流式）
根据 model 字段自动路由到对应后端
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from webui_llm_proxy.adapters.models import ChatRequest
from webui_llm_proxy.adapters.openai import OpenAIResponseAdapter, download_images
from webui_llm_proxy.api.dependencies import get_pool, parse_chat_request, verify_api_key
from webui_llm_proxy.api.server import get_event_bus, get_memory
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.config import settings
from webui_llm_proxy.core.event_bus import ChatEvent
from webui_llm_proxy.core.memory import MemoryManager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    chat_request: ChatRequest = Depends(parse_chat_request),
    _: bool = Depends(verify_api_key),
):
    """
    OpenAI 兼容：聊天补全接口
    """
    memory: MemoryManager = get_memory(request)
    event_bus = get_event_bus(request)

    user_message = chat_request.last_user_message
    stream_mode = chat_request.stream
    model = chat_request.model

    if not user_message and not chat_request.has_images:
        raise HTTPException(status_code=400, detail="No user message or images found")

    logger.info(f"Received request [model={model}, stream={stream_mode}, images={len(chat_request.image_urls)}]: {user_message[:100]}...")

    # 从池中获取客户端
    pool = get_pool(request, model)
    client = await pool.acquire()
    released = False

    async def _release():
        nonlocal released
        if not released:
            await pool.release(client)
            released = True

    # 下载多模态图片
    file_paths = []
    if chat_request.has_images and chat_request.image_urls:
        try:
            file_paths = await download_images(chat_request.image_urls)
            logger.info(f"Downloaded/decoded {len(file_paths)} images")
        except Exception as e:
            logger.warning(f"Image download failed: {e}")

    # 将历史消息加载到记忆
    for msg in chat_request.messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
            memory.add_message(msg["role"], "\n".join(texts))
        else:
            memory.add_message(msg["role"], content)

    # 保留请求体用于台账
    request_body_for_log = {
        "model": model,
        "messages": chat_request.messages,
        "stream": stream_mode,
    }

    adapter = OpenAIResponseAdapter()

    try:
        if stream_mode:
            # ========== 流式输出 ==========
            async def event_generator():
                full_response = ""
                msg_stream = client.send_message_stream(
                    user_message,
                    file_paths=file_paths if file_paths else None,
                    model_name=model,
                )
                stream = adapter.stream_response(
                    msg_stream,
                    model=model,
                    custom_content={"media_files": client.last_media_files} if client.last_media_files else None,
                )

                try:
                    async for chunk in stream:
                        yield chunk
                        # 累积完整响应
                        try:
                            data = json.loads(chunk.replace("data: ", "").strip())
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                full_response += delta["content"]
                        except Exception:
                            pass
                finally:
                    # 确保内部 generator 被关闭
                    await stream.aclose()
                    await msg_stream.aclose()

                    if full_response:
                        memory.add_message("assistant", full_response)
                        await event_bus.emit(
                            ChatEvent.STREAM_COMPLETED,
                            request=request_body_for_log,
                            response=full_response,
                        )

                    # 流结束后归还客户端
                    await _release()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        else:
            # ========== 非流式输出 ==========
            response_text = await client.send_message(
                user_message,
                file_paths=file_paths if file_paths else None,
                model_name=model,
            )

            from webui_llm_proxy.adapters.models import ChatResponse, MediaFile
            media_files = [
                MediaFile(
                    filename=m["filename"],
                    path=m["path"],
                    local_path=m["local_path"],
                    source=m["source"],
                    type=m["type"],
                )
                for m in client.last_media_files
            ]

            response = ChatResponse(
                content=response_text,
                model=model,
                media_files=media_files,
            )

            memory.add_message("assistant", response_text)
            await event_bus.emit(
                ChatEvent.RESPONSE_RECEIVED,
                request=request_body_for_log,
                response=response_text,
            )

            result = JSONResponse(content=adapter.build_response(response))
            await _release()
            return result

    except Exception as e:
        # 异常时也要归还客户端
        await _release()
        logger.error(f"Error processing request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

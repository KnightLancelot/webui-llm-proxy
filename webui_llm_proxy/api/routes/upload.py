"""
文件上传路由 — /v1/chat/completions/upload

支持上传图片、音频、视频、文档等文件，然后发送消息
"""

from __future__ import annotations

import json
import logging
import os
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from webui_llm_proxy.adapters.models import ChatResponse, MediaFile
from webui_llm_proxy.adapters.openai import OpenAIResponseAdapter
from webui_llm_proxy.api.dependencies import get_pool, verify_api_key
from webui_llm_proxy.api.server import get_event_bus, get_memory
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.config import settings
from webui_llm_proxy.core.event_bus import ChatEvent
from webui_llm_proxy.core.memory import MemoryManager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/chat/completions/upload")
async def chat_completions_upload(
    request: Request,
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    stream: bool = Form(default=False),
    model: str = Form(default=""),
    _: bool = Depends(verify_api_key),
):
    """
    多模态文件上传接口
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    # 修复编码
    message = message  # 编码修复在 parse_upload_request 中完成，但这里直接接收
    # 实际上 FastAPI 的 Form 已经解析了，我们手动修复
    from webui_llm_proxy.api.dependencies import fix_encoding
    message = fix_encoding(message)

    memory: MemoryManager = get_memory(request)
    event_bus = get_event_bus(request)

    # 从池中获取客户端
    pool = get_pool(request, model)
    client = await pool.acquire()
    released = False

    async def _release():
        nonlocal released
        if not released:
            await pool.release(client)
            released = True

    # 校验文件
    if len(files) > settings.upload.max_files:
        await _release()
        raise HTTPException(status_code=400, detail=f"Too many files. Max: {settings.upload.max_files}")

    max_size = settings.upload.max_file_size_bytes
    for f in files:
        if f.size > max_size:
            await _release()
            raise HTTPException(status_code=400, detail=f"File {f.filename} too large. Max: {settings.upload.max_size_mb}MB")

    # 保存上传文件
    os.makedirs(settings.upload.temp_dir, exist_ok=True)
    saved_paths = []

    try:
        for upload_file in files:
            ext = os.path.splitext(upload_file.filename)[1].lower()
            if ext not in settings.upload.allowed_extensions:
                await _release()
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported file type: {ext}",
                )

            temp_path = os.path.join(settings.upload.temp_dir, upload_file.filename)
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(upload_file.file, buffer)
            saved_paths.append(temp_path)
            logger.info(f"File saved: {upload_file.filename}")

        logger.info(f"Received upload request [model={model}, stream={stream}]: {message[:100]}... files: {len(saved_paths)}")

        upload_request_for_log = {
            "model": model,
            "message": message,
            "files": [f.filename for f in files],
            "stream": stream,
        }

        adapter = OpenAIResponseAdapter()

        if stream:
            # 流式输出
            async def event_generator():
                full_response = ""
                msg_stream = client.send_message_stream(
                    message,
                    file_paths=saved_paths,
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
                        try:
                            data = json.loads(chunk.replace("data: ", "").strip())
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                full_response += delta["content"]
                        except Exception:
                            pass
                finally:
                    await stream.aclose()
                    await msg_stream.aclose()

                    if full_response:
                        memory.add_message("assistant", full_response)
                        await event_bus.emit(
                            ChatEvent.STREAM_COMPLETED,
                            request=upload_request_for_log,
                            response=full_response,
                        )

                    # 清理临时文件
                    for p in saved_paths:
                        try:
                            os.remove(p)
                        except Exception:
                            pass

                    # 流结束后归还客户端
                    await _release()

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )

        else:
            # 非流式输出
            response_text = await client.send_message(
                message,
                file_paths=saved_paths,
                model_name=model,
            )

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
                model=model or settings.openai.model_name,
                media_files=media_files,
            )

            memory.add_message("assistant", response_text)
            await event_bus.emit(
                ChatEvent.RESPONSE_RECEIVED,
                request=upload_request_for_log,
                response=response_text,
            )

            # 清理临时文件
            for p in saved_paths:
                try:
                    os.remove(p)
                except Exception:
                    pass

            result = JSONResponse(content=adapter.build_response(response))
            await _release()
            return result

    except HTTPException:
        await _release()
        raise
    except Exception as e:
        await _release()
        for p in saved_paths:
            try:
                os.remove(p)
            except Exception:
                pass
        logger.error(f"Error processing upload request: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

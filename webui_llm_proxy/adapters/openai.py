"""
OpenAI 格式适配器 — 适配器模式的具体实现

将 OpenAI API 请求/响应格式与内部代理格式相互转换
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator

import aiohttp

from webui_llm_proxy.adapters.base import RequestAdapter, ResponseAdapter
from webui_llm_proxy.adapters.models import ChatRequest, ChatResponse
from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


def _generate_chat_id() -> str:
    """生成唯一的 chat completion ID"""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _generate_timestamp() -> int:
    """生成 Unix 时间戳（秒）"""
    return int(time.time())


class OpenAIRequestAdapter(RequestAdapter):
    """OpenAI 请求适配器"""

    def parse_request(self, body: dict) -> ChatRequest:
        messages = body.get("messages", [])
        model = body.get("model", settings.openai.model_name)
        stream = body.get("stream", False)
        temperature = body.get("temperature", 0.7)
        max_tokens = body.get("max_tokens")

        # 找到最后一条用户消息并解析多模态内容
        last_text = ""
        image_urls = []
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_text, image_urls = self._extract_content_parts(msg.get("content", ""))
                break

        return ChatRequest(
            messages=messages,
            model=model,
            stream=stream,
            temperature=temperature,
            max_tokens=max_tokens,
            last_user_message=last_text,
            image_urls=image_urls,
            has_images=len(image_urls) > 0,
        )

    @staticmethod
    def _extract_content_parts(content) -> tuple[str, list[str]]:
        """解析 OpenAI 多模态 content 字段"""
        if isinstance(content, str):
            return content, []

        if not isinstance(content, list):
            return str(content), []

        texts = []
        image_urls = []

        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text", "")
                if text:
                    texts.append(text)
            elif item_type == "image_url":
                img_obj = item.get("image_url", {})
                url = img_obj.get("url", "") if isinstance(img_obj, dict) else str(img_obj)
                if url:
                    image_urls.append(url)

        return "\n".join(texts), image_urls


class OpenAIResponseAdapter(ResponseAdapter):
    """OpenAI 响应适配器"""

    def build_response(self, response: ChatResponse) -> dict:
        message = {
            "role": "assistant",
            "content": response.content,
        }
        if response.media_files:
            message["custom_content"] = {
                "media_files": [
                    {
                        "filename": m.filename,
                        "path": m.path,
                        "local_path": m.local_path,
                        "source": m.source,
                        "type": m.type,
                    }
                    for m in response.media_files
                ]
            }

        return {
            "id": _generate_chat_id(),
            "object": "chat.completion",
            "created": _generate_timestamp(),
            "model": response.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(response.content) // 4,
                "completion_tokens": len(response.content) // 4,
                "total_tokens": len(response.content) // 2,
            },
        }

    def build_stream_chunk(
        self,
        delta: str,
        model: str,
        finish: bool = False,
        custom_content: dict | None = None,
    ) -> str:
        if finish:
            delta_obj = {}
            if custom_content:
                delta_obj["custom_content"] = custom_content
            data = {
                "id": _generate_chat_id(),
                "object": "chat.completion.chunk",
                "created": _generate_timestamp(),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta_obj,
                        "finish_reason": "stop",
                    }
                ],
            }
        else:
            data = {
                "id": _generate_chat_id(),
                "object": "chat.completion.chunk",
                "created": _generate_timestamp(),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": delta},
                        "finish_reason": None,
                    }
                ],
            }

        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def build_stream_end(self) -> str:
        return "data: [DONE]\n\n"

    async def stream_response(
        self,
        text_stream: AsyncGenerator[str, None],
        model: str,
        custom_content: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        # 发送第一个 chunk（role）
        first_chunk = {
            "id": _generate_chat_id(),
            "object": "chat.completion.chunk",
            "created": _generate_timestamp(),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

        # 发送内容 chunks
        async for chunk in text_stream:
            if chunk:
                yield self.build_stream_chunk(chunk, model)

        # 发送结束标记
        yield self.build_stream_chunk("", model, finish=True, custom_content=custom_content)
        yield self.build_stream_end()


async def download_images(image_urls: list[str], temp_dir: str | None = None) -> list[str]:
    """
    下载/解码图片并保存为临时文件
    支持 data:image/xxx;base64,... 和普通 HTTP URL

    Args:
        image_urls: 图片 URL 列表
        temp_dir: 临时文件保存目录

    Returns:
        本地文件路径列表
    """
    temp_dir = temp_dir or settings.upload.temp_dir
    os.makedirs(temp_dir, exist_ok=True)

    saved_paths = []
    for idx, url in enumerate(image_urls):
        try:
            # Base64 data URL
            if url.startswith("data:image/"):
                match = re.match(r"data:image/(\w+);base64,(.+)", url)
                if match:
                    ext, b64 = match.groups()
                    data = base64.b64decode(b64)
                    path = os.path.join(temp_dir, f"multimodal_{idx}_{uuid.uuid4().hex[:8]}.{ext}")
                    with open(path, "wb") as f:
                        f.write(data)
                    saved_paths.append(path)
                continue

            # 普通 HTTP URL
            if url.startswith(("http://", "https://")):
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            content_type = resp.headers.get("Content-Type", "")
                            ext_map = {
                                "image/jpeg": "jpg",
                                "image/png": "png",
                                "image/gif": "gif",
                                "image/webp": "webp",
                                "image/bmp": "bmp",
                            }
                            ext = "jpg"
                            for ct, e in ext_map.items():
                                if ct in content_type:
                                    ext = e
                                    break
                            path = os.path.join(temp_dir, f"multimodal_{idx}_{uuid.uuid4().hex[:8]}.{ext}")
                            with open(path, "wb") as f:
                                f.write(data)
                            saved_paths.append(path)
                continue

            # 本地文件路径
            if os.path.isfile(url):
                saved_paths.append(url)

        except Exception as e:
            logger.warning(f"Image download failed [{url[:60]}...]: {e}")
            continue

    return saved_paths

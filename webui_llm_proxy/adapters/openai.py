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


class FunctionCallPromptBuilder:
    """根据 OpenAI tools 定义生成模型 prompt 中的工具说明"""

    _TOOL_INSTRUCTION = (
        "你可以使用以下工具。当需要调用工具时，请只输出如下格式的 JSON 代码块，"
        "不要包含其他解释：\n"
        "```json\n"
        '{"tool_calls": [{"function": {"name": "工具名", "arguments": {"参数名": "值"}}}]}\n'
        "```\n\n"
        "可用工具："
    )

    @classmethod
    def build(cls, tools: list[dict], tool_choice: str | None = None) -> str:
        """根据 tools 与 tool_choice 生成 prompt 片段，无 tools 时返回空字符串"""
        if not tools:
            return ""
        lines = [cls._TOOL_INSTRUCTION]
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            func = tool.get("function", {})
            name = func.get("name", "")
            desc = func.get("description", "")
            params = func.get("parameters", {})
            param_desc = cls._describe_schema(params)
            lines.append(f"- {name}({param_desc}): {desc}")

        choice_hint = cls._tool_choice_hint(tool_choice)
        if choice_hint:
            lines.append(choice_hint)

        return "\n".join(lines)

    @classmethod
    def _tool_choice_hint(cls, tool_choice: str | None) -> str:
        if not tool_choice or tool_choice == "auto":
            return ""
        if tool_choice == "none":
            return "\n当前不需要调用工具，请直接回答。"
        if tool_choice in ("required", "any"):
            return "\n你必须调用至少一个工具来回答。"
        if isinstance(tool_choice, dict):
            name = tool_choice.get("function", {}).get("name", "")
            if name:
                return f"\n你必须调用工具：{name}"
        if isinstance(tool_choice, str):
            return f"\n你必须调用工具：{tool_choice}"
        return ""

    @classmethod
    def _describe_schema(cls, schema: dict) -> str:
        """把 JSON Schema 的 parameters 简化成 human-readable 描述"""
        if not isinstance(schema, dict):
            return ""
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        parts = []
        for key, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            ptype = prop.get("type", "any")
            marker = "required" if key in required else "optional"
            parts.append(f"{key}: {ptype}({marker})")
        return ", ".join(parts)


class ToolCallParser:
    """从模型原始文本中解析 function call，输出 OpenAI 兼容格式"""

    # 常见模型输出前缀，先剥离再解析
    _KNOWN_PREFIXES = (
        "Gemini 说JSON",
        "Gemini 说 JSON",
        "Gemini 说",
        "JSON",
        "说JSON",
        "说 JSON",
        "`json",
        "`",
    )

    @classmethod
    def parse(cls, text: str) -> list[dict]:
        """解析文本中的 tool_calls，返回 OpenAI 格式列表；未命中返回 []"""
        if not text or not isinstance(text, str):
            return []

        # 1. Markdown JSON 代码块
        for match in re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL):
            try:
                data = json.loads(match.group(1).strip())
                calls = cls._extract_calls(data)
                if calls:
                    return calls
            except Exception:
                continue

        # 2. 从文本中提取以 { 或 [ 开头、包含 tool_calls / function 的平衡 JSON
        # 先去掉常见前缀，避免前缀影响 marker 查找
        stripped_text = cls._strip_prefixes(text)
        for marker in ('{"tool_calls"', '[{"function"', '[{"tool_calls"'):
            idx = stripped_text.find(marker)
            if idx != -1:
                extracted = cls._extract_balanced_json(stripped_text[idx:])
                if extracted:
                    try:
                        data = json.loads(extracted)
                        calls = cls._extract_calls(data)
                        if calls:
                            return calls
                    except Exception:
                        # JSON 格式损坏时尝试 fallback 正则提取
                        calls = cls._fallback_extract_calls(stripped_text[idx:])
                        if calls:
                            return calls

        # 3. XML 标签
        xml_match = re.search(r"<tool_calls>(.*?)</tool_calls>", text, re.DOTALL)
        if xml_match:
            try:
                data = json.loads(xml_match.group(1).strip())
                calls = cls._extract_calls(data)
                if calls:
                    return calls
            except Exception:
                pass

        return []

    @classmethod
    def _strip_prefixes(cls, text: str) -> str:
        """去掉模型输出里常见的前缀，返回尽可能干净的 JSON 文本"""
        text = text.strip()
        lowered = text.lower()
        # 去掉冒号/空格分隔的前缀行
        for prefix in cls._KNOWN_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
            # 兼容 "Gemini 说JSON" 后面跟冒号的情况
            if lowered.startswith(prefix.lower() + ":"):
                text = text[len(prefix) + 1:].strip()
                break
        return text

    @classmethod
    def _extract_balanced_json(cls, text: str) -> str:
        """从文本开头提取一个平衡的 JSON 对象或数组（支持嵌套）"""
        if not text:
            return ""
        first_char = text[0]
        if first_char == "{":
            close_char = "}"
        elif first_char == "[":
            close_char = "]"
        else:
            return ""

        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == first_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    return text[: i + 1]
        return ""

    @classmethod
    def _fallback_extract_calls(cls, text: str) -> list[dict]:
        """
        当 JSON 解析失败时的兜底提取。
        先按最外层 {} / [] 切分 call 对象，再用正则抓 name/arguments。
        arguments 若无法解析为合法 JSON，则保留原始字符串。
        """
        # 只取最外层结构：以 { 或 [ 开头，找到匹配的闭合括号
        outer = cls._extract_balanced_json(text.strip())
        if not outer:
            return []

        # 尝试拆成单个 call 对象
        call_texts: list[str] = []
        first_char = outer[0]
        if first_char == "{":
            # 对象形式：{"tool_calls": [{...}, {...}]}
            # 先找 tool_calls 数组
            tc_match = re.search(r'"tool_calls"\s*:\s*(\[)', outer)
            if tc_match:
                arr_start = tc_match.start(1)
                arr_text = cls._extract_balanced_json(outer[arr_start:])
                if arr_text:
                    call_texts = cls._split_json_array(arr_text)
            else:
                call_texts = [outer]
        elif first_char == "[":
            call_texts = cls._split_json_array(outer)

        result = []
        for call_text in call_texts:
            name = cls._extract_json_string_value(call_text, "name")
            if not name:
                continue

            # 找到 "arguments" 后第一个 { 开始的内容
            args_match = re.search(r'"arguments"\s*:\s*(\{)', call_text)
            if not args_match:
                # 也许 arguments 本身就是字符串
                args_str = cls._extract_json_string_value(call_text, "arguments")
                if args_str is None:
                    args_str = "{}"
            else:
                args_start = args_match.start(1)
                args_raw = cls._extract_balanced_json(call_text[args_start:])
                if not args_raw:
                    args_raw = "{}"
                # 尝试修复常见错误：未转义的双引号
                args_str = cls._try_repair_json(args_raw)

            call_id = f"call_{uuid.uuid4().hex[:16]}"
            result.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args_str,
                },
            })
        return result

    @classmethod
    def _split_json_array(cls, array_text: str) -> list[str]:
        """把一个 JSON 数组文本拆成单个元素字符串列表（不严格校验 JSON）"""
        if not array_text or array_text[0] != "[" or array_text[-1] != "]":
            return []
        inner = array_text[1:-1].strip()
        if not inner:
            return []

        parts = []
        depth = 0
        in_string = False
        escape = False
        current = []
        for ch in inner:
            if in_string:
                current.append(ch)
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                current.append(ch)
            elif ch in ("{", "["):
                depth += 1
                current.append(ch)
            elif ch in ("}", "]"):
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current = []
            else:
                current.append(ch)

        part = "".join(current).strip()
        if part:
            parts.append(part)
        return parts

    @classmethod
    def _extract_json_string_value(cls, text: str, key: str) -> str | None:
        """用简单正则提取某个字符串字段的原始值（不含外层引号）"""
        pattern = rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"'
        match = re.search(pattern, text)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _try_repair_json(cls, raw: str) -> str:
        """尝试把损坏的 JSON 修成合法字符串；修不好就原样返回。"""
        try:
            data = json.loads(raw)
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            pass

        # 常见修复：把字符串里未转义的双引号前补反斜杠（仅对简单情况有效）
        # 这里只做一次保守尝试：去掉控制字符后重新 load
        try:
            cleaned = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f]', '', raw)
            data = json.loads(cleaned)
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            pass

        return raw

    @classmethod
    def _extract_calls(cls, data) -> list[dict]:
        if isinstance(data, dict):
            calls = data.get("tool_calls", [])
        elif isinstance(data, list):
            calls = data
        else:
            return []

        result = []
        for call in calls:
            if not isinstance(call, dict):
                continue
            # 兼容 {"function": {...}} 与扁平 {"name": ..., "arguments": ...}
            func = call.get("function", {})
            if not func:
                func = call
            name = func.get("name") or call.get("name")
            arguments = func.get("arguments")
            if arguments is None:
                arguments = call.get("arguments")
            if not name:
                continue
            if isinstance(arguments, dict):
                arguments = json.dumps(arguments, ensure_ascii=False)
            elif arguments is None:
                arguments = "{}"
            else:
                arguments = str(arguments)
            call_id = call.get("id") or f"call_{uuid.uuid4().hex[:16]}"
            result.append({
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            })
        return result


class OpenAIRequestAdapter(RequestAdapter):
    """OpenAI 请求适配器"""

    def parse_request(self, body: dict) -> ChatRequest:
        messages = body.get("messages", [])
        model = body.get("model", settings.openai.model_name)
        stream = body.get("stream", False)
        temperature = body.get("temperature", 0.7)
        max_tokens = body.get("max_tokens")
        tools = body.get("tools", []) or []
        tool_choice = body.get("tool_choice", "auto")

        if tools:
            last_text, image_urls = self._build_model_prompt(messages, tools, tool_choice)
        else:
            # 无 tools 时保持原有行为：system + 最后一条 user
            system_texts = []
            for msg in messages:
                if msg.get("role") == "system":
                    text, _ = self._extract_content_parts(msg.get("content", ""))
                    if text:
                        system_texts.append(text)
            system_content = "\n\n".join(system_texts)

            last_text = ""
            image_urls = []
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    user_text, image_urls = self._extract_content_parts(msg.get("content", ""))
                    if system_content:
                        last_text = f"{system_content}\n\n{user_text}"
                    else:
                        last_text = user_text
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
            tools=tools,
            tool_choice=tool_choice if isinstance(tool_choice, str) else str(tool_choice),
        )

    def _build_model_prompt(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str | None,
    ) -> tuple[str, list[str]]:
        """
        当请求包含 tools 时，构造包含完整上下文和工具说明的 prompt。

        输出顺序：system 指令 -> 工具说明 -> 历史对话（user/assistant/tool）-> 最后一条 user。
        """
        system_texts = []
        conversation_lines: list[str] = []
        last_user_text = ""
        last_user_images: list[str] = []
        found_last_user = False

        for msg in reversed(messages):
            role = msg.get("role")
            content = msg.get("content", "")
            text, urls = self._extract_content_parts(content)

            if role == "user" and not found_last_user:
                last_user_text = text
                last_user_images = urls
                found_last_user = True
            elif role == "system" and text:
                system_texts.insert(0, text)
            elif role == "tool" and text:
                call_id = msg.get("tool_call_id", "unknown")
                name = msg.get("name", "unknown")
                conversation_lines.insert(0, f"[Tool result for {call_id} - {name}]\n{text}")
            elif role == "assistant":
                if msg.get("tool_calls"):
                    rendered = json.dumps({"tool_calls": msg["tool_calls"]}, ensure_ascii=False)
                    conversation_lines.insert(0, f"[Assistant called tools]\n{rendered}")
                elif text:
                    conversation_lines.insert(0, f"[Assistant]\n{text}")
            elif role == "user" and text:
                conversation_lines.insert(0, f"[User]\n{text}")

        tool_instruction = FunctionCallPromptBuilder.build(tools, tool_choice)

        parts: list[str] = []
        if system_texts:
            parts.append("\n\n".join(system_texts))
        if tool_instruction:
            parts.append(tool_instruction)
        if conversation_lines:
            parts.append("\n\n".join(conversation_lines))
        if last_user_text:
            parts.append(f"[User]\n{last_user_text}")

        final_text = "\n\n".join(parts)
        return final_text, last_user_images

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
        if response.tool_calls:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": response.tool_calls,
            }
            finish_reason = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": response.content,
            }
            finish_reason = "stop"
        if response.reasoning_content:
            message["reasoning_content"] = response.reasoning_content
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
                    "finish_reason": finish_reason,
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
        tool_calls: list[dict] | None = None,
    ) -> str:
        if finish:
            delta_obj: dict = {}
            if custom_content:
                delta_obj["custom_content"] = custom_content
            if tool_calls:
                delta_obj["tool_calls"] = tool_calls
            data = {
                "id": _generate_chat_id(),
                "object": "chat.completion.chunk",
                "created": _generate_timestamp(),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": delta_obj,
                        "finish_reason": "tool_calls" if tool_calls else "stop",
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

    def build_role_chunk(self, model: str) -> str:
        """流式响应第一个 chunk：只返回 role"""
        data = {
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
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def build_finish_chunk(
        self,
        model: str,
        custom_content: dict | None = None,
        tool_calls: list[dict] | None = None,
    ) -> str:
        """流式响应结束 chunk：可携带 tool_calls"""
        return self.build_stream_chunk("", model, finish=True, custom_content=custom_content, tool_calls=tool_calls)

    def build_stream_end(self) -> str:
        return "data: [DONE]\n\n"

    async def stream_response(
        self,
        text_stream: AsyncGenerator[str, None],
        model: str,
        custom_content: dict | None = None,
        tool_calls: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        # 发送第一个 chunk（role）
        yield self.build_role_chunk(model)

        # 发送内容 chunks
        async for chunk in text_stream:
            if chunk:
                yield self.build_stream_chunk(chunk, model)

        # 发送结束标记
        yield self.build_finish_chunk(model, custom_content=custom_content, tool_calls=tool_calls)
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

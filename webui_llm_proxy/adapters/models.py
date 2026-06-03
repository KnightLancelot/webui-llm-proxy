"""
适配器层数据模型 — 类型安全的请求/响应数据结构
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChatRequest:
    """解析后的聊天请求"""
    messages: list[dict]
    model: str
    stream: bool = False
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    last_user_message: str = ""
    image_urls: list[str] = field(default_factory=list)
    has_images: bool = False


@dataclass
class MediaFile:
    """提取的媒体文件信息"""
    filename: str
    path: str  # URL 路径（如 /media/xxx.png）
    local_path: str
    source: str
    type: str = "image"


@dataclass
class ChatResponse:
    """内部聊天响应"""
    content: str
    model: str
    media_files: list[MediaFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "model": self.model,
            "media_files": [
                {
                    "filename": m.filename,
                    "path": m.path,
                    "local_path": m.local_path,
                    "source": m.source,
                    "type": m.type,
                }
                for m in self.media_files
            ],
        }

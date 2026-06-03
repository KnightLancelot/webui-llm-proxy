"""
记忆管理器 — 单例模式 (Singleton Pattern)

管理对话的短期记忆（当前会话）和长期记忆（跨会话摘要）。
使用 __new__ 确保全局唯一实例。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    长短期记忆管理器（单例）

    - 短期记忆：当前对话的最近 N 轮消息（保留完整上下文）
    - 长期记忆：跨会话的摘要信息（当短期记忆过长时自动压缩）
    """

    _instance: Optional[MemoryManager] = None
    _initialized = False

    def __new__(cls, memory_file: Optional[str] = None) -> MemoryManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, memory_file: Optional[str] = None) -> None:
        if MemoryManager._initialized:
            return

        self.memory_file = memory_file or settings.memory.memory_file
        self.short_term: List[dict] = []
        self.long_term: List[dict] = []
        self._load()
        MemoryManager._initialized = True

    def add_message(self, role: str, content: str, session_id: str = "default") -> None:
        """
        添加一条消息到短期记忆

        Args:
            role: 'user' 或 'assistant'
            content: 消息内容
            session_id: 会话标识
        """
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
        }
        self.short_term.append(message)
        logger.debug(f"Added message [{role}]: {content[:50]}...")

        # 检查是否需要压缩到长期记忆
        self._maybe_compress()

    def get_context(self, max_rounds: Optional[int] = None) -> str:
        """
        获取用于发送给模型的上下文文本

        包含：长期记忆摘要 + 最近的短期记忆
        """
        max_rounds = max_rounds or settings.memory.short_term_rounds
        parts = []

        # 长期记忆摘要
        if self.long_term and settings.memory.enable_long_term:
            parts.append("【历史摘要】")
            for summary in self.long_term[-3:]:
                parts.append(f"- {summary['content']}")
            parts.append("")

        # 短期记忆
        recent = self.short_term[-max_rounds * 2:]
        if recent:
            parts.append("【当前对话】")
            for msg in recent:
                role_name = "用户" if msg["role"] == "user" else "助手"
                parts.append(f"{role_name}: {msg['content']}")
            parts.append("")

        return "\n".join(parts)

    def get_openai_messages(self, max_rounds: Optional[int] = None) -> List[dict]:
        """返回 OpenAI 格式的消息列表"""
        max_rounds = max_rounds or settings.memory.short_term_rounds
        recent = self.short_term[-max_rounds * 2:]
        return [{"role": m["role"], "content": m["content"]} for m in recent]

    def clear_short_term(self) -> None:
        """清空短期记忆"""
        logger.info("Short-term memory cleared")
        self.short_term = []

    def clear_all(self) -> None:
        """清空所有记忆"""
        logger.info("All memory cleared")
        self.short_term = []
        self.long_term = []
        self._save()

    def get_status(self) -> dict:
        """获取记忆状态"""
        return {
            "short_term_count": len(self.short_term),
            "long_term_count": len(self.long_term),
            "recent_messages": [
                {"role": m["role"], "content": m["content"][:100]}
                for m in self.short_term[-6:]
            ],
        }

    def _maybe_compress(self) -> None:
        """当短期记忆过长时，自动摘要并转移到长期记忆"""
        threshold = settings.memory.long_term_threshold
        if len(self.short_term) < threshold * 2:
            return

        if not settings.memory.enable_long_term:
            self.short_term = self.short_term[-threshold * 2:]
            return

        to_compress = self.short_term[:threshold]
        summary_text = self._create_summary(to_compress)

        self.long_term.append({
            "content": summary_text,
            "timestamp": datetime.now().isoformat(),
            "message_count": len(to_compress),
        })

        self.short_term = self.short_term[threshold:]
        logger.info(f"Compressed {len(to_compress)} messages to long-term memory")
        self._save()

    @staticmethod
    def _create_summary(messages: List[dict]) -> str:
        """创建消息摘要"""
        first_user = next((m for m in messages if m["role"] == "user"), None)
        last_assistant = next((m for m in reversed(messages) if m["role"] == "assistant"), None)

        parts = []
        if first_user:
            parts.append(f"用户询问: {first_user['content'][:80]}...")
        if last_assistant:
            parts.append(f"助手回复: {last_assistant['content'][:80]}...")

        return " | ".join(parts) if parts else "历史对话摘要"

    def _load(self) -> None:
        """从文件加载记忆"""
        if not os.path.exists(self.memory_file):
            return
        try:
            with open(self.memory_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.long_term = data.get("long_term", [])
                logger.info(f"Loaded long-term memory: {len(self.long_term)} summaries")
        except Exception as e:
            logger.warning(f"Failed to load memory file: {e}")

    def _save(self) -> None:
        """保存记忆到文件"""
        try:
            os.makedirs(os.path.dirname(self.memory_file) or ".", exist_ok=True)
            data = {
                "long_term": self.long_term,
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save memory file: {e}")

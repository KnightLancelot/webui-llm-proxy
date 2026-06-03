"""
使用台账记录器 — 观察者模式的具体实现

按日期在 JSONL 文件中记录请求/响应。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from webui_llm_proxy.config import settings
from webui_llm_proxy.core.event_bus import ChatEvent, EventContext, EventObserver

logger = logging.getLogger(__name__)


class UsageLogger(EventObserver):
    """
    使用台账记录器（观察者）

    监听 RESPONSE_RECEIVED 和 STREAM_COMPLETED 事件，
    将请求/响应记录到按日期分文件的 JSONL 日志中。
    """

    def __init__(self, log_dir: str | None = None) -> None:
        self.log_dir = log_dir or settings.log.usage_log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    async def on_event(self, event: ChatEvent, context: EventContext) -> None:
        if event not in (ChatEvent.RESPONSE_RECEIVED, ChatEvent.STREAM_COMPLETED):
            return

        request = context.get("request")
        response = context.get("response")

        if not request or not response:
            return

        self._save(request, response)

    def _save(self, request: dict, response: str) -> None:
        """保存一条记录到日志文件"""
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(self.log_dir, f"{date_str}.log")

            record = {
                "timestamp": datetime.now().isoformat(),
                "request": request,
                "response": response,
            }

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            logger.debug(f"Usage log recorded: {log_file}")
        except Exception as e:
            logger.warning(f"Failed to save usage log: {e}")

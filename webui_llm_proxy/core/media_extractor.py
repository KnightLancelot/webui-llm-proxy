"""
媒体文件提取器 — 观察者模式的具体实现

监听 MEDIA_EXTRACTED 事件，处理媒体文件的后续操作（如记录、通知等）。
实际的媒体提取逻辑在 KimiClient._extract_media_files() 中，
此模块负责提取后的观察者处理。
"""

from __future__ import annotations

import logging

from webui_llm_proxy.core.event_bus import ChatEvent, EventContext, EventObserver

logger = logging.getLogger(__name__)


class MediaExtractorObserver(EventObserver):
    """
    媒体文件提取观察者

    监听 MEDIA_EXTRACTED 事件，记录提取到的媒体文件信息。
    """

    async def on_event(self, event: ChatEvent, context: EventContext) -> None:
        if event != ChatEvent.MEDIA_EXTRACTED:
            return

        media_files = context.get("media_files", [])
        if not media_files:
            return

        logger.info(f"Media extraction event: {len(media_files)} files")
        for f in media_files:
            logger.info(f"  - {f.get('filename', 'unknown')}: {f.get('path', 'unknown')}")

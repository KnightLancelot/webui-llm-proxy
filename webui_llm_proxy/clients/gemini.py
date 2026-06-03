"""
Gemini Web UI Client - Migrated from gemini_proxy/gemini_client.py

Production-hardened DOM interaction for Gemini Web UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import AsyncGenerator, Callable, Optional

from webui_llm_proxy.browser.controller import BrowserController
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.config import settings
from webui_llm_proxy.core.detection_strategies import StableCountStrategy

logger = logging.getLogger(__name__)


class GeminiClient(BaseLLMClient):
    """Gemini Web UI client."""

    def __init__(
        self,
        browser: Optional[BrowserController] = None,
        detection_strategy: Optional[StableCountStrategy] = None,
    ) -> None:
        browser = browser or BrowserController()
        detection = detection_strategy or StableCountStrategy(
            threshold=3,
            idle_timeout=settings.gemini.stream_idle_timeout,
        )
        super().__init__(browser, detection)

    # ==================== Required hooks ====================

    def _get_chat_url(self) -> str:
        return settings.gemini.chat_url

    def _get_browser_profile(self) -> str:
        return settings.browser.user_data_dir

    def _get_page_load_wait(self) -> int:
        return settings.gemini.page_load_wait

    def _get_response_start_timeout(self) -> int:
        return settings.gemini.response_start_timeout

    def _get_stream_idle_timeout(self) -> int:
        return settings.gemini.stream_idle_timeout

    def _get_poll_interval(self) -> float:
        return settings.gemini.poll_interval_ms / 1000.0

    # ==================== Input & Send ====================

    async def _input_message_impl(self, message: str) -> None:
        page = self._get_page()
        input_box = page.locator('div[role="textbox"]').first
        try:
            await input_box.wait_for(state="visible", timeout=10000)
        except Exception:
            await input_box.wait_for(state="attached", timeout=10000)
            await input_box.scroll_into_view_if_needed()
            await asyncio.sleep(1)
        await input_box.fill(message)
        await asyncio.sleep(0.5)

    async def _click_send_impl(self) -> None:
        page = self._get_page()
        send_selectors = [
            'button.send-button',
            'button[type="submit"]',
            'button[aria-label*="发送"]',
            'button[aria-label*="Send"]',
            'button svg[xmlns]',
            'div[role="button"]:has(svg)',
        ]
        for sel in send_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    logger.info(f"Send button clicked: {sel}")
                    return
            except Exception:
                continue
        logger.info("Trying Enter key to send")
        input_box = page.locator('div[role="textbox"]').first
        await input_box.press("Enter")
        await asyncio.sleep(0.5)
        await input_box.press("Enter")

    # ==================== Upload ====================

    async def _upload_files_impl(self, file_paths: list[str]) -> bool:
        if not file_paths:
            return True
        logger.info(f"Uploading {len(file_paths)} files to Gemini...")
        page = self._get_page()
        try:
            file_input = page.locator('input[type="file"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files(file_paths)
                logger.info("Files set to input[type=file]")
                await asyncio.sleep(2)
                return True

            # Click upload button
            upload_selectors = [
                'button[aria-label*="Upload"]',
                'button[aria-label*="上传"]',
                'button[title*="Upload"]',
                '[data-testid="upload-button"]',
                'button:has-text("+")',
            ]
            upload_btn = None
            for sel in upload_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        upload_btn = btn
                        break
                except Exception:
                    continue

            if not upload_btn:
                # Inject hidden input
                await page.evaluate(
                    """() => {
                        if (!document.getElementById('__gemini_proxy_file_input__')) {
                            const input = document.createElement('input');
                            input.type = 'file';
                            input.id = '__gemini_proxy_file_input__';
                            input.style.display = 'none';
                            document.body.appendChild(input);
                        }
                    }"""
                )
                injected = page.locator("#__gemini_proxy_file_input__").first
                if await injected.count() > 0:
                    await injected.set_input_files(file_paths)
                    await page.evaluate(
                        """() => {
                            const input = document.getElementById('__gemini_proxy_file_input__');
                            input.dispatchEvent(new Event('change', { bubbles: true }));
                        }"""
                    )
                    await asyncio.sleep(2)
                    return True
                return False

            async with page.expect_file_chooser(timeout=20000) as fc_info:
                await upload_btn.click()
                await asyncio.sleep(1)
                menu_items = [
                    'button:has-text("Upload file")',
                    'button:has-text("上传文件")',
                    '[role="menuitem"]:has-text("Upload")',
                ]
                for item_sel in menu_items:
                    try:
                        item = page.locator(item_sel).first
                        if await item.is_visible(timeout=3000):
                            await item.click()
                            break
                    except Exception:
                        continue
            file_chooser = await fc_info.value
            await file_chooser.set_files(file_paths)
            logger.info("Files uploaded via file chooser")
            await asyncio.sleep(2)
            return True
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return False

    # ==================== Text extraction ====================

    async def _extract_response_text(self, skip_count: int = 0) -> str:
        page = self._get_page()
        try:
            responses = await page.locator(".response-content").all()
            if responses and len(responses) > skip_count:
                text = await responses[-1].text_content()
                return text.strip() if text else ""
            return ""
        except Exception as e:
            logger.debug(f"Extract response failed: {e}")
            return ""

    # ==================== New chat ====================

    async def new_chat(self) -> None:
        logger.info("Starting new chat (Gemini)...")
        page = self._get_page()
        selectors = [
            'button:has-text("New chat")',
            'a:has-text("New chat")',
            '[aria-label*="New chat"]',
            '[data-testid="new-chat-button"]',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click()
                    logger.info("New chat button clicked")
                    await asyncio.sleep(3)
                    return
            except Exception:
                continue
        logger.warning("New chat button not found, skipping")

    # ==================== Diff ====================

    @staticmethod
    def _diff_text(old: str, new: str) -> str:
        if old == new:
            return ""
        if new.startswith(old):
            return new[len(old) :]
        return new


LLMClientFactory.register("gemini", GeminiClient)

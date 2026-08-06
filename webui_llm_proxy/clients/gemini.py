"""
Gemini Web UI Client - Migrated from gemini_proxy/gemini_client.py

Production-hardened DOM interaction for Gemini Web UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
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

    # ==================== Long message handling ====================

    async def _prepare_long_message(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
    ) -> tuple[str, list[str]]:
        """
        Gemini 对超长输入框粘贴支持较差：当 prompt 超过阈值时，
        将其写入 .txt 文件并上传，输入框只保留简短引导语。
        """
        threshold = settings.gemini.long_message_threshold
        if threshold <= 0 or len(message) <= threshold:
            return message, list(file_paths or [])

        temp_dir = settings.upload.temp_dir or "./data/uploads"
        os.makedirs(temp_dir, exist_ok=True)
        filename = f"gemini_prompt_{uuid.uuid4().hex[:16]}.txt"
        filepath = os.path.join(temp_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(message)
        logger.info(
            f"Gemini message too long ({len(message)} chars > {threshold}), "
            f"saved prompt to {filepath} and will upload it."
        )

        all_paths = list(file_paths or [])
        all_paths.append(filepath)
        short_message = (
            "我已将完整指令上传到附件的文本文件中，请阅读该文件并按其中的要求执行。"
        )
        return short_message, all_paths

    async def send_message(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
        model_name: Optional[str] = None,
    ) -> str:
        message, file_paths = await self._prepare_long_message(message, file_paths)
        return await super().send_message(message, file_paths=file_paths, model_name=model_name)

    async def send_message_stream(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
        model_name: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncGenerator[str, None]:
        message, file_paths = await self._prepare_long_message(message, file_paths)
        async for chunk in super().send_message_stream(
            message,
            file_paths=file_paths,
            model_name=model_name,
            on_chunk=on_chunk,
        ):
            yield chunk

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
        """Gemini: 每次调用前回到首页，确保开启新会话。"""
        logger.info("Preparing chat (Gemini)...")
        try:
            page = self._get_page()
            current = page.url
            if settings.gemini.chat_url in current:
                logger.info(f"Already on Gemini page: {current}")
                # 如果当前 URL 包含具体 chat id，仍然需要刷新首页以开启新会话
                if "/app/" in current and current != settings.gemini.chat_url:
                    logger.info("Current URL contains chat id, navigating to home for fresh session")
                else:
                    return
            await page.goto(settings.gemini.chat_url, wait_until="domcontentloaded")
            await asyncio.sleep(self._get_page_load_wait())
            logger.info(f"Navigated to Gemini home: {settings.gemini.chat_url}")
        except Exception as e:
            logger.warning(f"Gemini navigation to home failed: {e}, trying new chat button")
            await self._click_new_chat_button()

    async def _click_new_chat_button(self) -> None:
        """点击 New chat 按钮作为回退方案"""
        try:
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
        except Exception as e:
            logger.warning(f"Click new chat button failed: {e}")

    async def _cleanup_after_send(self) -> None:
        """每次调用后回到 Gemini 首页，确保下一次调用是新会话。"""
        logger.info("Cleaning up Gemini session...")
        try:
            page = self._get_page()
            await page.goto(settings.gemini.chat_url, wait_until="domcontentloaded")
            await asyncio.sleep(self._get_page_load_wait())
            logger.info("Gemini returned to home after chat")
        except Exception as e:
            logger.warning(f"Gemini cleanup failed: {e}")

    # ==================== Diff ====================

    @staticmethod
    def _diff_text(old: str, new: str) -> str:
        if old == new:
            return ""
        if new.startswith(old):
            return new[len(old) :]
        return new


LLMClientFactory.register("gemini", GeminiClient)

"""
Kimi Web UI Client - Migrated from gemini_proxy/kimi_client.py

Uses JS-probed coordinates for send-button clicking, DOM-state completion
detection, and precise assistant-message selectors for text extraction.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import time
import uuid
from typing import AsyncGenerator, Callable, Optional

from playwright.async_api import Page

from webui_llm_proxy.browser.controller import BrowserController
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.config import settings
from webui_llm_proxy.core.detection_strategies import DOMStateStrategy

logger = logging.getLogger(__name__)

# ===== 媒体文件类型映射 =====
_CONTENT_TYPE_EXT_MAP = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/svg+xml": "svg",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.ms-powerpoint": "ppt",
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/csv": "csv",
    "application/zip": "zip",
    "application/x-rar-compressed": "rar",
    "application/x-7z-compressed": "7z",
    "application/octet-stream": "bin",
}

_EXT_TYPE_MAP = {
    "jpg": "image",
    "jpeg": "image",
    "png": "image",
    "gif": "image",
    "webp": "image",
    "bmp": "image",
    "svg": "image",
    "xlsx": "spreadsheet",
    "xls": "spreadsheet",
    "csv": "spreadsheet",
    "docx": "document",
    "doc": "document",
    "txt": "document",
    "pptx": "presentation",
    "ppt": "presentation",
    "pdf": "pdf",
    "zip": "archive",
    "rar": "archive",
    "7z": "archive",
}


def _ext_from_content_type(ct: str) -> str:
    """从 Content-Type 推断扩展名"""
    if not ct:
        return "bin"
    ct = ct.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT_MAP.get(ct, "bin")


def _ext_from_url(url: str) -> str | None:
    """从 URL 路径推断扩展名"""
    path = url.split("?")[0].split("#")[0]
    name = os.path.basename(path)
    if "." in name:
        return name.rsplit(".", 1)[-1].lower()
    return None


def _file_type_from_ext(ext: str) -> str:
    """从扩展名推断文件类型"""
    return _EXT_TYPE_MAP.get(ext.lower(), "file")


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符"""
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.strip()
    # 避免路径遍历
    name = os.path.basename(name)
    return name or "download"


def _resolve_filepath(media_dir: str, filename: str) -> str:
    """解析不冲突的文件路径"""
    filepath = os.path.join(media_dir, filename)
    if not os.path.exists(filepath):
        return filepath
    base, ext = os.path.splitext(filename)
    counter = 1
    while True:
        new_name = f"{base}_{counter}{ext}"
        new_path = os.path.join(media_dir, new_name)
        if not os.path.exists(new_path):
            return new_path
        counter += 1


class KimiClient(BaseLLMClient):
    """
    Kimi Web UI client with production-hardened DOM interaction logic.
    """

    def __init__(
        self,
        browser: Optional[BrowserController] = None,
        detection_strategy: Optional[DOMStateStrategy] = None,
    ) -> None:
        browser = browser or BrowserController()
        detection = detection_strategy or DOMStateStrategy(
            poll_interval_ms=settings.kimi.poll_interval_ms,
        )
        super().__init__(browser, detection)

    # ==================== Required hooks ====================

    def _get_chat_url(self) -> str:
        return settings.kimi.chat_url

    def _get_browser_profile(self) -> str:
        return settings.browser.kimi_user_data_dir

    def _get_page_load_wait(self) -> int:
        return settings.kimi.page_load_wait

    def _get_response_start_timeout(self) -> int:
        return settings.kimi.response_start_timeout

    def _get_stream_idle_timeout(self) -> int:
        return settings.kimi.stream_idle_timeout

    def _get_poll_interval(self) -> float:
        return settings.kimi.poll_interval_ms / 1000.0

    # ==================== Page lifecycle helpers ====================

    async def _ensure_page_alive(self) -> None:
        """Restore page from browser context if the current one closed."""
        page = self._get_page()
        try:
            if page and not page.is_closed():
                return
        except Exception:
            pass
        logger.warning("Page closed, restoring from context...")
        ctx = self._browser.context
        if ctx and ctx.pages:
            self._browser._page = ctx.pages[-1]
            logger.info(f"Restored page: {self._get_page().url}")
        else:
            raise RuntimeError("Context closed, cannot restore page")

    async def _check_kimi_reply_state(self) -> dict:
        """
        Probe DOM to detect whether the assistant reply is finished.
        Returns: {'finished': bool, 'generating': bool, ...}
        """
        page = self._get_page()
        try:
            return await page.evaluate(
                """() => {
                    const actionBar = document.querySelector('.segment-assistant-actions-content');
                    const hasActionBar = !!actionBar && actionBar.getBoundingClientRect().width > 0;
                    const lastNode = document.querySelector('.segment-content-box.last-node');
                    const hasLoading = !!lastNode;
                    return {
                        finished: hasActionBar,
                        generating: hasLoading && !hasActionBar,
                        has_action_bar: hasActionBar,
                        has_loading: hasLoading
                    };
                }"""
            )
        except Exception as e:
            logger.debug(f"DOM state probe failed: {e}")
            return {"finished": False, "generating": False}

    # ==================== Session management ====================

    async def new_chat(self) -> None:
        """Kimi: stay on current page if already on kimi.moonshot.cn."""
        logger.info("Preparing chat (Kimi)...")
        try:
            await self._ensure_page_alive()
            page = self._get_page()
            current = page.url
            if "kimi.moonshot.cn" in current:
                logger.info(f"Already on Kimi page: {current}")
                return
            logger.info("Navigating to Kimi home page")
            await page.goto(settings.kimi.chat_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
        except Exception as e:
            logger.warning(f"Prepare chat failed: {e}")

    async def delete_current_chat(self) -> None:
        """Delete the current conversation from Kimi sidebar."""
        try:
            await self._ensure_page_alive()
            page = self._get_page()
            current_url = page.url
            match = re.search(r"/chat/([a-zA-Z0-9_-]+)", current_url)
            if not match:
                logger.info("No chat ID in URL, skip deletion")
                return
            chat_id = match.group(1)
            short_id = chat_id[:8]
            logger.info(f"Deleting chat {short_id}...")

            result = await page.evaluate(
                """(chatId) => {
                    const short = chatId.slice(0, 8);
                    let item = null;
                    // strategy A: href match
                    let links = document.querySelectorAll('a[href*="' + chatId + '"]');
                    if (!links.length) links = document.querySelectorAll('a[href*="' + short + '"]');
                    if (links.length) { item = links[0].closest('div, li') || links[0].parentElement; }
                    // strategy B: data-id
                    if (!item) {
                        document.querySelectorAll('div[data-id], li[data-id]').forEach(el => {
                            const did = el.getAttribute('data-id') || '';
                            if (!item && (did.includes(chatId) || did.includes(short))) item = el;
                        });
                    }
                    // strategy C: active item
                    if (!item) {
                        document.querySelectorAll('[class*="active"], [aria-selected="true"]').forEach(el => {
                            const r = el.getBoundingClientRect();
                            if (!item && r.width > 50) item = el;
                        });
                    }
                    if (!item) return {found: false, reason: 'item_not_found'};

                    let moreBtn = null;
                    item.querySelectorAll('button, div, svg, span').forEach(el => {
                        const t = (el.textContent || '').trim();
                        const c = (el.className || '').toString().toLowerCase();
                        if (t === '…' || t === '...' || c.includes('more') || c.includes('action') || c.includes('menu')) {
                            moreBtn = el;
                        }
                    });
                    if (moreBtn) moreBtn.click();
                    else item.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));

                    return new Promise((resolve) => {
                        setTimeout(() => {
                            const keywords = ['删除', 'Delete'];
                            let best = null, bestArea = Infinity;
                            document.querySelectorAll('*').forEach(el => {
                                const text = (el.textContent || '').trim().replace(/\\s+/g, ' ');
                                const cls = (el.className || '').toString().toLowerCase();
                                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                                let matched = false;
                                for (const kw of keywords) {
                                    if (text === kw || text === kw.toLowerCase() || aria === kw.toLowerCase() || cls.includes('delete') || cls.includes('remove') || cls.includes('trash')) {
                                        matched = true; break;
                                    }
                                }
                                if (matched) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 5 && r.height > 5) {
                                        const area = r.width * r.height;
                                        if (area < bestArea) { bestArea = area; best = el; }
                                    }
                                }
                            });
                            if (best) {
                                best.click();
                                resolve({found: true, moreClicked: !!moreBtn});
                            } else {
                                resolve({found: false, reason: 'delete_not_found'});
                            }
                        }, 800);
                    });
                }""",
                chat_id,
            )
            if not result.get("found"):
                logger.warning(f"Delete failed: {result.get('reason')}")
                return
            logger.info("Delete button clicked, waiting for confirmation...")
            await asyncio.sleep(1.5)

            confirm = await page.evaluate(
                """() => {
                    const keywords = ['确认删除', '确定删除', 'Confirm Delete', 'Delete', '删除'];
                    const all = document.querySelectorAll('button, div[role="button"], a');
                    for (const el of all) {
                        const text = (el.textContent || '').trim();
                        const cls = (el.className || '').toString().toLowerCase();
                        for (const kw of keywords) {
                            if ((text === kw || text.includes(kw)) && (cls.includes('confirm') || cls.includes('danger') || cls.includes('primary') || cls.includes('ok'))) {
                                const r = el.getBoundingClientRect();
                                if (r.width > 20 && r.height > 10 && r.x >= 0 && r.y >= 0) {
                                    return {found: true, x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), text};
                                }
                            }
                        }
                    }
                    return {found: false};
                }"""
            )
            if confirm.get("found"):
                await page.mouse.click(confirm["x"], confirm["y"])
                await asyncio.sleep(1)
            logger.info("Chat deletion finished")
        except Exception as e:
            logger.warning(f"Delete chat error: {e}")

    # ==================== Upload ====================

    async def _wait_for_attachment_visible(self, file_paths: list, timeout: int = 20) -> bool:
        page = self._get_page()
        filenames = [os.path.basename(p).lower() for p in file_paths]
        start = time.time()
        while time.time() - start < timeout:
            try:
                result = await page.evaluate(
                    """(filenames) => {
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            const text = (el.textContent || '').toLowerCase();
                            for (const name of filenames) {
                                if (text.includes(name)) {
                                    const r = el.getBoundingClientRect();
                                    if (r.width > 20 && r.height > 10 && r.x >= 0 && r.y >= 0) return {found: true};
                                }
                            }
                        }
                        return {found: false};
                    }""",
                    filenames,
                )
                if result.get("found"):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def _wait_for_upload_complete(self, file_paths: list) -> bool:
        try:
            if not await self._wait_for_attachment_visible(file_paths, timeout=15):
                logger.warning("Attachment cards not detected, fallback wait")
                await asyncio.sleep(5)
            try:
                total_size = sum(os.path.getsize(p) for p in file_paths)
                exts = [os.path.splitext(p)[1].lower() for p in file_paths]
                is_media = any(ext in ('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.mp4', '.mov', '.avi', '.mkv', '.webm') for ext in exts)
                extra_wait = min(45, max(8, total_size / (1024 * 1024) * 2.0)) if is_media else min(15, max(3, total_size / (1024 * 1024) * 0.5))
            except Exception:
                extra_wait = 10
            logger.info(f"Attachments rendered, waiting {extra_wait:.1f}s for server upload...")
            await asyncio.sleep(extra_wait)
            return True
        except Exception as e:
            logger.warning(f"Upload wait error: {e}, fallback 10s")
            await asyncio.sleep(10)
            return True

    async def _upload_files_impl(self, file_paths: list[str]) -> bool:
        if not file_paths:
            return True
        logger.info(f"Uploading {len(file_paths)} files to Kimi...")
        try:
            await self._ensure_page_alive()
            page = self._get_page()

            # Strategy 1: existing file inputs
            file_inputs = await page.locator('input[type="file"]').all()
            if file_inputs:
                for fi in file_inputs:
                    try:
                        await fi.set_input_files(file_paths)
                        if await self._wait_for_upload_complete(file_paths):
                            return True
                    except Exception:
                        continue

            # Strategy 2: click '+' button -> trigger file input
            plus_info = await page.evaluate(
                """() => {
                    const input = document.querySelector('textarea, div[contenteditable="true"]');
                    if (!input) return {found: false};
                    const ir = input.getBoundingClientRect();
                    let best = null;
                    document.querySelectorAll('*').forEach(el => {
                        const r = el.getBoundingClientRect();
                        const nearLeft = r.x >= ir.x - 20 && r.x <= ir.x + 40;
                        const nearBottom = r.y >= ir.y + ir.height - 50 && r.y <= ir.y + ir.height + 40;
                        if (nearLeft && nearBottom && r.width < 40 && r.height < 40 && r.width > 10) {
                            const cls = (el.className || '').toString().toLowerCase();
                            const isAdd = cls.includes('add') || cls.includes('plus') || cls.includes('upload') || cls.includes('toolkit');
                            const hasSvg = el.querySelector('svg') !== null;
                            if (!best || isAdd || hasSvg) {
                                best = {x: r.x, y: r.y, w: r.width, h: r.height};
                            }
                        }
                    });
                    return best ? {found: true, ...best} : {found: false};
                }"""
            )
            if not plus_info.get("found"):
                logger.warning("'+' button not found")
                return False

            await page.mouse.click(plus_info["x"] + plus_info["w"] / 2, plus_info["y"] + plus_info["h"] / 2)
            logger.info("Clicked '+' button")
            await asyncio.sleep(2)

            file_inputs_after = await page.locator('input[type="file"]').all()
            if file_inputs_after:
                try:
                    await file_inputs_after[0].set_input_files(file_paths)
                    return await self._wait_for_upload_complete(file_paths)
                except Exception as e:
                    logger.warning(f"Set files to dynamic input failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Upload failed: {e}")
            return False

    # ==================== Input & Send ====================

    async def _input_message_impl(self, message: str) -> None:
        page = self._get_page()
        input_el = page.locator("textarea, div[contenteditable='true']").first
        try:
            await input_el.fill(message)
            logger.debug("fill() succeeded")
        except Exception as e:
            logger.warning(f"fill() failed, fallback to JS: {e}")
            await page.evaluate(
                """(msg) => {
                    const editors = document.querySelectorAll('[contenteditable="true"], textarea');
                    for (const el of editors) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 200 && rect.height > 20) {
                            el.focus();
                            if (el.tagName === 'TEXTAREA') el.value = msg;
                            else el.innerText = msg;
                            el.dispatchEvent(new InputEvent('input', {bubbles: true, data: msg}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }""",
                message,
            )
        await asyncio.sleep(0.5)

    async def _click_send_impl(self) -> None:
        page = self._get_page()
        send_btn_info = await page.evaluate(
            """() => {
                const inputBox = document.querySelector('textarea, div[contenteditable="true"]');
                if (!inputBox) return {found: false};
                const inputRect = inputBox.getBoundingClientRect();

                const sendBtn = document.querySelector('.send-button-container');
                if (sendBtn) {
                    const r = sendBtn.getBoundingClientRect();
                    return {found: true, x: r.x, y: r.y, w: r.width, h: r.height, strategy: 'class'};
                }

                const all = document.querySelectorAll('*');
                let best = null;
                for (const el of all) {
                    const rect = el.getBoundingClientRect();
                    const nearRight = rect.x >= inputRect.x + inputRect.width - 80 && rect.x <= inputRect.x + inputRect.width + 40;
                    const nearBottom = rect.y >= inputRect.y - 20 && rect.y <= inputRect.y + inputRect.height + 40;
                    const isSmall = rect.width >= 20 && rect.width <= 60 && rect.height >= 20 && rect.height <= 60;
                    if (nearRight && nearBottom && isSmall) {
                        const html = el.innerHTML || '';
                        const hasSvg = html.includes('<svg');
                        const clsLower = (typeof el.className === 'string' ? el.className : '').toLowerCase();
                        const score = (hasSvg ? 10 : 0) + (clsLower.includes('send') ? 5 : 0);
                        if (!best || score > best.score) {
                            best = {tag: el.tagName, x: rect.x, y: rect.y, w: rect.width, h: rect.height, score};
                        }
                    }
                }
                return best ? {found: true, ...best, strategy: 'coordinate'} : {found: false};
            }"""
        )
        if send_btn_info.get("found"):
            logger.info(f"Send button at ({send_btn_info['x']:.0f}, {send_btn_info['y']:.0f}), strategy={send_btn_info.get('strategy')}")
            await page.mouse.click(send_btn_info["x"] + send_btn_info["w"] / 2, send_btn_info["y"] + send_btn_info["h"] / 2)
            logger.info("Send button clicked")
        else:
            logger.warning("Send button not found, trying Enter key")
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.5)

    # ==================== Stream response (overrides base) ====================

    async def _stream_response(
        self,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncGenerator[str, None]:
        page = self._get_page()
        old_responses = await page.locator(".chat-message-assistant, .assistant-message, [data-testid='assistant-message'], .message-content").all()
        old_response_count = len(old_responses)

        last_text = ""
        idle_start = time.time()
        stable_count = 0
        started = False
        min_reply_length = 50
        response_start_time = None
        ABSOLUTE_MAX_WAIT = 300

        while True:
            await asyncio.sleep(self._get_poll_interval())
            current_text = await self._extract_response_text()

            if not current_text or len(current_text) < min_reply_length:
                if not started and time.time() - idle_start > self._get_response_start_timeout():
                    logger.warning("Response start timeout")
                    break
                continue

            if not started and len(current_text) >= min_reply_length:
                started = True
                response_start_time = time.time()
                logger.debug(f"Stream started, length={len(current_text)}")

            if current_text != last_text:
                prev_len = len(last_text or "")
                if prev_len > 50 and current_text and len(current_text) < prev_len * 0.8:
                    logger.debug(f"Length drop {prev_len} -> {len(current_text)}, ignored")
                else:
                    new_content = self._diff_text(last_text, current_text)
                    if new_content:
                        yield new_content
                        if on_chunk:
                            on_chunk(new_content)
                    last_text = current_text
                    idle_start = time.time()
                    stable_count = 0
            else:
                stable_count += 1

            state = await self._check_kimi_reply_state()
            if state.get("finished"):
                logger.info(f"Stream complete (action bar detected), total={len(last_text)}")
                break
            if state.get("generating") and stable_count > 0:
                logger.debug("Generating marker detected, reset stable_count")
                stable_count = 0
                idle_start = time.time()

            if time.time() - idle_start > self._get_stream_idle_timeout():
                logger.debug("Stream idle timeout")
                break
            if started and response_start_time and time.time() - response_start_time > ABSOLUTE_MAX_WAIT:
                logger.warning(f"Absolute timeout {ABSOLUTE_MAX_WAIT}s, forcing end")
                break

        logger.info(f"[Stream] Response complete, total length: {len(last_text)}")

    # ==================== Text extraction ====================

    async def _extract_response_text(self, skip_count: int = 0) -> str:
        page = self._get_page()
        precise_selectors = [
            ".chat-content-item-assistant",
            ".segment-assistant",
            ".markdown-container",
            ".markdown",
        ]
        for sel in precise_selectors:
            try:
                responses = await page.locator(sel).all()
                if responses:
                    text = await responses[-1].text_content()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                continue
        return ""

    # ==================== Media extraction ====================

    async def _extract_media_files(self) -> list:
        media_files: list[dict] = []
        page = self._get_page()
        media_dir = settings.media_dir
        os.makedirs(media_dir, exist_ok=True)

        await asyncio.sleep(3.0)

        # ========== 阶段 A：图片提取（独立异常隔离）==========
        try:
            images = await page.evaluate(
                """() => {
                    const selectors = ['.chat-content-item-assistant', '.segment-assistant', '.markdown-container', '.markdown'];
                    const results = [];
                    const seen = new Set();
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        if (!els.length) continue;
                        const el = els[els.length - 1];
                        el.querySelectorAll('img').forEach(img => {
                            const src = img.src || '';
                            const rect = img.getBoundingClientRect();
                            if (src && !seen.has(src) && rect.width > 1 && rect.height > 1) {
                                seen.add(src);
                                results.push({src, alt: img.alt || '', width: img.naturalWidth || 0, height: img.naturalHeight || 0});
                            }
                        });
                    }
                    if (!results.length) {
                        document.querySelectorAll('img').forEach(img => {
                            const rect = img.getBoundingClientRect();
                            const src = img.src || '';
                            if (src && !seen.has(src) && rect.width > 1 && rect.height > 1) {
                                seen.add(src);
                                results.push({src, alt: img.alt || '', width: img.naturalWidth || 0, height: img.naturalHeight || 0});
                            }
                        });
                    }
                    return results;
                }"""
            )

            # 防御 page.evaluate 返回 None 的情况
            if not images:
                images = []

            for idx, img_info in enumerate(images):
                try:
                    src = img_info["src"]
                    ext = "png"
                    data = None

                    if src.startswith("data:image/"):
                        match = re.match(r"data:image/(\w+);base64,(.+)", src)
                        if match:
                            ext, b64 = match.groups()
                            data = base64.b64decode(b64)
                    elif src.startswith("blob:"):
                        b64_result = await page.evaluate(
                            """(url) => fetch(url).then(r => r.blob()).then(b => new Promise((resolve) => { const reader = new FileReader(); reader.onloadend = () => resolve(reader.result); reader.readAsDataURL(b); })).catch(e => ({error: e.message}));""",
                            src,
                        )
                        if isinstance(b64_result, str) and b64_result.startswith("data:"):
                            header, b64 = b64_result.split(",", 1)
                            if "jpeg" in header:
                                ext = "jpg"
                            elif "png" in header:
                                ext = "png"
                            elif "webp" in header:
                                ext = "webp"
                            elif "gif" in header:
                                ext = "gif"
                            data = base64.b64decode(b64)
                    elif src.startswith(("http://", "https://")):
                        try:
                            import aiohttp
                            async with aiohttp.ClientSession() as session:
                                async with session.get(src, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                                    if resp.status == 200:
                                        data = await resp.read()
                                        ct = resp.headers.get("Content-Type", "")
                                        if "jpeg" in ct:
                                            ext = "jpg"
                                        elif "png" in ct:
                                            ext = "png"
                                        elif "webp" in ct:
                                            ext = "webp"
                                        elif "gif" in ct:
                                            ext = "gif"
                        except Exception as e:
                            logger.warning(f"aiohttp download failed: {e}")
                        if not data:
                            b64_result = await page.evaluate(
                                """(url) => fetch(url, {credentials: 'include'}).then(r => r.ok ? r.blob() : {error: 'HTTP ' + r.status}).then(b => b.error ? b : new Promise((resolve) => { const reader = new FileReader(); reader.onloadend = () => resolve(reader.result); reader.readAsDataURL(b); })).catch(e => ({error: e.message}));""",
                                src,
                            )
                            if isinstance(b64_result, str) and b64_result.startswith("data:"):
                                header, b64 = b64_result.split(",", 1)
                                if "jpeg" in header:
                                    ext = "jpg"
                                elif "png" in header:
                                    ext = "png"
                                elif "webp" in header:
                                    ext = "webp"
                                elif "gif" in header:
                                    ext = "gif"
                                data = base64.b64decode(b64)

                    if data:
                        filename = f"kimi_img_{idx}_{uuid.uuid4().hex[:8]}.{ext}"
                        filepath = os.path.join(media_dir, filename)
                        with open(filepath, "wb") as f:
                            f.write(data)
                        media_files.append({
                            "type": "image",
                            "path": f"/media/{filename}",
                            "filename": filename,
                            "local_path": filepath,
                            "source": src[:200],
                        })
                        logger.info(f"Extracted image: {filename} ({len(data)} bytes)")
                except Exception as e:
                    logger.warning(f"Image extraction failed: {e}")
        except Exception as e:
            logger.warning(f"Image list extraction failed: {e}")

        # ========== 阶段 B：sandbox 文件提取（独立异常隔离）==========
        sandbox_files: list[dict] = []
        try:
            # 增强检测：尝试多次，给异步渲染留出时间
            for attempt in range(3):
                if attempt > 0:
                    logger.info(f"Sandbox detection retry {attempt}/2, waiting 2s...")
                    await asyncio.sleep(2.0)

                detected = await page.evaluate(
                    r"""() => {
                        const results = [];
                        const seen = new Set();

                        // 1. 从标准 a[href] 链接中提取
                        document.querySelectorAll('a[href]').forEach(link => {
                            const href = (link.getAttribute('href') || '').toLowerCase();
                            if ((href.includes('sandbox') || href.includes('agents/output')) && !seen.has(href)) {
                                seen.add(href);
                                results.push({
                                    type: 'link',
                                    href: link.getAttribute('href'),
                                    text: (link.textContent || '').trim(),
                                    selector: 'a[href]'
                                });
                            }
                        });

                        // 2. 从 data-* 属性中提取（Vue/React 可能把真实 URL 放在 data 属性里）
                        ['data-href', 'data-url', 'data-src', 'data-file', 'data-path'].forEach(attr => {
                            document.querySelectorAll('[' + attr + ']').forEach(el => {
                                const val = (el.getAttribute(attr) || '').toLowerCase();
                                if ((val.includes('sandbox') || val.includes('agents/output')) && !seen.has(val)) {
                                    seen.add(val);
                                    results.push({
                                        type: 'data',
                                        href: el.getAttribute(attr),
                                        text: (el.textContent || '').trim(),
                                        selector: '[' + attr + ']'
                                    });
                                }
                            });
                        });

                        // 3. 从文本节点中提取 sandbox:// 路径
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
                        let node;
                        while (node = walker.nextNode()) {
                            const text = node.textContent || '';
                            const matches = text.match(/sandbox:\/\/[^\s"'<>]+/g);
                            if (matches) {
                                matches.forEach(m => {
                                    if (!seen.has(m)) {
                                        seen.add(m);
                                        results.push({type: 'text', href: m, text: '', selector: 'textNode'});
                                    }
                                });
                            }
                        }

                        // 4. 从图片 src 中提取
                        document.querySelectorAll('img[src]').forEach(img => {
                            const src = (img.getAttribute('src') || '').toLowerCase();
                            if ((src.includes('agents/output') || src.includes('sandbox')) && !seen.has(src)) {
                                seen.add(src);
                                results.push({type: 'img', href: img.getAttribute('src'), text: '', selector: 'img[src]'});
                            }
                        });

                        // 调试信息：返回检测统计
                        return {
                            results: results,
                            linkCount: document.querySelectorAll('a[href]').length,
                            allHrefs: Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')).filter(h => h && (h.includes('sandbox') || h.includes('output')))
                        };
                    }"""
                )

                # 防御 page.evaluate 返回 None
                if not detected:
                    detected = {"results": [], "linkCount": 0, "allHrefs": []}

                raw_results = detected.get("results", []) if isinstance(detected, dict) else []
                link_count = detected.get("linkCount", 0) if isinstance(detected, dict) else 0
                all_hrefs = detected.get("allHrefs", []) if isinstance(detected, dict) else []

                logger.info(f"Sandbox detection attempt {attempt + 1}: found {len(raw_results)} refs, total links={link_count}, matched hrefs={all_hrefs}")

                if raw_results:
                    sandbox_files = raw_results
                    break

            if sandbox_files:
                logger.info(f"Detected {len(sandbox_files)} sandbox references")
                for sf in sandbox_files:
                    url = sf.get("href", "")
                    link_text = sf.get("text", "")
                    selector = sf.get("selector", "")
                    logger.info(f"Sandbox ref [type={sf.get('type')}, selector={selector}]: {url[:100]}")
                    if not url:
                        continue

                    download_result = None

                    # 方式1: fetch + dataURL（适合中小文件）
                    try:
                        result = await page.evaluate(
                            """async (args) => {
                                const url = args.url;
                                try {
                                    const response = await fetch(url, {credentials: 'include'});
                                    if (!response.ok) {
                                        return {error: 'HTTP ' + response.status};
                                    }
                                    const contentType = response.headers.get('content-type') || '';
                                    const blob = await response.blob();
                                    return await new Promise((resolve) => {
                                        const reader = new FileReader();
                                        reader.onloadend = () => resolve({
                                            dataUrl: reader.result,
                                            contentType: contentType,
                                            size: blob.size
                                        });
                                        reader.onerror = () => resolve({error: 'FileReader error'});
                                        reader.readAsDataURL(blob);
                                    });
                                } catch (e) {
                                    return {error: e.message};
                                }
                            }""",
                            {"url": url},
                        )

                        if isinstance(result, dict) and "dataUrl" in result:
                            header, b64 = result["dataUrl"].split(",", 1)
                            data = base64.b64decode(b64)
                            content_type = result.get("contentType", "")

                            url_ext = _ext_from_url(url)
                            ct_ext = _ext_from_content_type(content_type)
                            ext = url_ext or ct_ext or "bin"

                            original_name = os.path.basename(url.split("?")[0].split("#")[0])
                            if link_text and not original_name:
                                original_name = link_text
                            original_name = _sanitize_filename(original_name)
                            if not original_name or "." not in original_name:
                                original_name = f"download_{uuid.uuid4().hex[:8]}.{ext}"
                            else:
                                base, _ = os.path.splitext(original_name)
                                original_name = f"{base}.{ext}"

                            filepath = _resolve_filepath(media_dir, original_name)
                            with open(filepath, "wb") as f:
                                f.write(data)

                            actual_name = os.path.basename(filepath)
                            actual_ext = _ext_from_url(f"/fake.{actual_name}") or ext
                            file_type = _file_type_from_ext(actual_ext)

                            download_result = {
                                "type": file_type,
                                "path": f"/media/{actual_name}",
                                "filename": actual_name,
                                "local_path": filepath,
                                "source": url,
                            }
                            logger.info(f"Downloaded sandbox file: {actual_name} ({len(data)} bytes)")
                        else:
                            logger.warning(f"Fetch failed for {url}: {result}")
                    except Exception as e:
                        logger.warning(f"Fetch download error for {url}: {e}")

                    # 方式2: 通过浏览器原生下载机制触发（备用）
                    # sandbox:// 协议浏览器直接 fetch 不了，但 <a download> 可能能触发浏览器内部处理
                    if not download_result:
                        try:
                            original_name = os.path.basename(url.split("?")[0].split("#")[0])
                            if link_text and not original_name:
                                original_name = link_text
                            original_name = _sanitize_filename(original_name)
                            if not original_name or "." not in original_name:
                                original_name = f"download_{uuid.uuid4().hex[:8]}.bin"

                            downloads: list = []

                            def _on_download(download):
                                downloads.append(download)

                            page.on("download", _on_download)
                            try:
                                await page.evaluate(
                                    """async (args) => {
                                        const url = args.url;
                                        const filename = args.filename;
                                        try {
                                            const a = document.createElement('a');
                                            a.href = url;
                                            a.download = filename;
                                            a.style.display = 'none';
                                            document.body.appendChild(a);
                                            a.click();
                                            setTimeout(() => {
                                                if (a.parentNode) document.body.removeChild(a);
                                            }, 2000);
                                        } catch (err) {
                                            console.error('Download trigger error:', err);
                                        }
                                    }""",
                                    {"url": url, "filename": original_name},
                                )

                                # 等待浏览器下载事件
                                for _ in range(50):
                                    if downloads:
                                        break
                                    await asyncio.sleep(0.2)

                                if downloads:
                                    download = downloads[0]
                                    suggested = download.suggested_filename or original_name
                                    suggested = _sanitize_filename(suggested)
                                    filepath = _resolve_filepath(media_dir, suggested)
                                    await download.save_as(filepath)
                                    actual_name = os.path.basename(filepath)
                                    actual_ext = _ext_from_url(f"/fake.{actual_name}") or "bin"
                                    download_result = {
                                        "type": _file_type_from_ext(actual_ext),
                                        "path": f"/media/{actual_name}",
                                        "filename": actual_name,
                                        "local_path": filepath,
                                        "source": url,
                                    }
                                    logger.info(f"Browser-downloaded sandbox file: {actual_name}")
                            finally:
                                page.remove_listener("download", _on_download)
                        except Exception as e:
                            logger.warning(f"Browser download fallback failed for {url}: {e}")

                    # 方式3: 直接点击链接，Playwright 捕获浏览器下载事件
                    if not download_result:
                        try:
                            original_name = os.path.basename(url.split("?")[0].split("#")[0])
                            if link_text and not original_name:
                                original_name = link_text
                            original_name = _sanitize_filename(original_name)
                            if not original_name or "." not in original_name:
                                original_name = f"download_{uuid.uuid4().hex[:8]}.bin"

                            # 先查找链接元素
                            link_locator = page.locator(f'a[href="{url}"]').first
                            if await link_locator.count() == 0:
                                link_locator = page.locator(f'a[href*="{url.replace("sandbox://", "")}"]').first

                            if await link_locator.count() > 0:
                                logger.info(f"Trying click-download for {url}")
                                async with page.expect_download(timeout=15000) as download_info:
                                    await link_locator.click()
                                download = await download_info.value
                                suggested = download.suggested_filename or original_name
                                suggested = _sanitize_filename(suggested)
                                filepath = _resolve_filepath(media_dir, suggested)
                                await download.save_as(filepath)
                                actual_name = os.path.basename(filepath)
                                actual_ext = _ext_from_url(f"/fake.{actual_name}") or "bin"
                                download_result = {
                                    "type": _file_type_from_ext(actual_ext),
                                    "path": f"/media/{actual_name}",
                                    "filename": actual_name,
                                    "local_path": filepath,
                                    "source": url,
                                }
                                logger.info(f"Click-downloaded sandbox file: {actual_name}")
                            else:
                                logger.warning(f"Link element not found for {url}")
                        except Exception as e:
                            logger.warning(f"Click download failed for {url}: {e}")

                    # 方式4: 模拟 UI 点击预览面板下载按钮（最终备用）
                    if not download_result:
                        try:
                            original_name = os.path.basename(url.split("?")[0].split("#")[0])
                            if link_text and not original_name:
                                original_name = link_text
                            original_name = _sanitize_filename(original_name)
                            if not original_name or "." not in original_name:
                                original_name = f"download_{uuid.uuid4().hex[:8]}.bin"

                            link_locator = page.locator(f'a[href="{url}"]').first
                            if await link_locator.count() == 0:
                                link_locator = page.locator(f'a[href*="{url.replace("sandbox://", "")}"]').first

                            if await link_locator.count() > 0:
                                logger.info(f"Trying preview-panel download for {url}")
                                # 点击链接打开预览面板
                                await link_locator.click()
                                await asyncio.sleep(3)  # 等待预览面板加载

                                download_btn = None
                                find_reason = ""

                                # ===== 策略A: 通过文字 "下载" / "Download" 查找 =====
                                if not download_btn:
                                    for text_kw in ["下载", "Download"]:
                                        try:
                                            # Playwright get_by_text 支持中文模糊匹配
                                            candidate = page.get_by_text(text_kw, exact=False).first
                                            if await candidate.count() > 0:
                                                tag = await candidate.evaluate("el => el.tagName.toLowerCase()")
                                                role = await candidate.get_attribute("role") or ""
                                                if tag in ("button", "a") or role == "button":
                                                    download_btn = candidate
                                                    find_reason = f"text='{text_kw}'"
                                                    break
                                                # 向上找 clickable 祖先
                                                parent = candidate.locator('xpath=ancestor::button[1] | ancestor::a[1] | ancestor::div[@role="button"][1]').first
                                                if await parent.count() > 0:
                                                    download_btn = parent
                                                    find_reason = f"text='{text_kw}'-ancestor"
                                                    break
                                        except Exception:
                                            continue
                                        if download_btn:
                                            break

                                # ===== 策略B: 通过 aria-label / title 查找 =====
                                if not download_btn:
                                    for attr in ["aria-label", "title"]:
                                        for kw in ["下载", "Download", "download"]:
                                            try:
                                                candidate = page.locator(f'[{attr}*="{kw}"]').first
                                                if await candidate.count() > 0:
                                                    tag = await candidate.evaluate("el => el.tagName.toLowerCase()")
                                                    role = await candidate.get_attribute("role") or ""
                                                    if tag in ("button", "a") or role == "button":
                                                        download_btn = candidate
                                                        find_reason = f"{attr}='{kw}'"
                                                        break
                                            except Exception:
                                                continue
                                        if download_btn:
                                            break

                                # ===== 策略C: 更通用的 SVG 下载图标检测（带位置过滤）=====
                                if not download_btn:
                                    try:
                                        viewport = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
                                        all_svgs = await page.locator('svg').all()
                                        for svg in all_svgs:
                                            try:
                                                # 先过滤位置：只保留页面右侧上半部分的 SVG（预览面板操作区）
                                                svg_box = await svg.bounding_box()
                                                if not svg_box or svg_box['width'] < 5 or svg_box['height'] < 5:
                                                    continue
                                                if svg_box['x'] < viewport['w'] * 0.6 or svg_box['y'] > viewport['h'] * 0.25:
                                                    continue

                                                paths = await svg.locator('path').all()
                                                for path in paths:
                                                    d = await path.get_attribute('d') or ""
                                                    d_lower = d.lower()
                                                    # 下载箭头常见 path 关键字（收紧，避免匹配普通图标）
                                                    if any(k in d_lower for k in ('down', 'arrow', 'download', 'save', 'm512')):
                                                        for xpath in ('xpath=ancestor::button[1]', 'xpath=ancestor::a[1]', 'xpath=ancestor::div[@role="button"][1]'):
                                                            parent = svg.locator(xpath).first
                                                            if await parent.count() > 0:
                                                                p_box = await parent.bounding_box()
                                                                if p_box and p_box['width'] > 10 and p_box['height'] > 10:
                                                                    # 父元素也必须在右侧区域，防止匹配到左侧导航栏图标
                                                                    if p_box['x'] > viewport['w'] * 0.6:
                                                                        download_btn = parent
                                                                        find_reason = "svg-download-icon"
                                                                        break
                                                            if download_btn:
                                                                break
                                                        if download_btn:
                                                            break
                                            except Exception:
                                                continue
                                            if download_btn:
                                                break
                                    except Exception:
                                        pass

                                # ===== 策略E: 通过 name="Download" + 位置过滤 精确匹配预览面板下载按钮 =====
                                if not download_result:
                                    try:
                                        all_svgs = await page.locator('svg[name="Download"]').all()
                                        viewport = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
                                        target_svg = None
                                        for svg in all_svgs:
                                            try:
                                                box = await svg.bounding_box()
                                                if box and box['x'] > viewport['w'] * 0.5:
                                                    target_svg = svg
                                                    break
                                            except Exception:
                                                continue

                                        if target_svg:
                                            logger.info("Found preview-panel download button by svg[name='Download'] (right side), dispatching click event...")
                                            async with page.expect_download(timeout=15000) as download_info:
                                                await target_svg.evaluate("""el => {
                                                    const evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                                                    el.dispatchEvent(evt);
                                                }""")
                                            download = await download_info.value
                                            suggested = download.suggested_filename or original_name
                                            suggested = _sanitize_filename(suggested)
                                            filepath = _resolve_filepath(media_dir, suggested)
                                            await download.save_as(filepath)
                                            actual_name = os.path.basename(filepath)
                                            actual_ext = _ext_from_url(f"/fake.{actual_name}") or "bin"
                                            download_result = {
                                                "type": _file_type_from_ext(actual_ext),
                                                "path": f"/media/{actual_name}",
                                                "filename": actual_name,
                                                "local_path": filepath,
                                                "source": url,
                                            }
                                            logger.info(f"Preview-panel downloaded via event dispatch: {actual_name}")
                                    except Exception as e:
                                        logger.warning(f"Event dispatch on right-side svg[name='Download'] failed: {e}")

                                # ===== 策略D: 坐标候选法 —— 直接获取页面右侧顶部可点击元素并逐一尝试 =====
                                if not download_result:
                                    try:
                                        candidates = await page.evaluate("""() => {
                                            const candidates = [];
                                            const all = document.querySelectorAll('button, [role="button"], a, svg, i, span');
                                            for (const el of all) {
                                                const rect = el.getBoundingClientRect();
                                                if (rect.width < 8 || rect.height < 8) continue;
                                                const vw = window.innerWidth, vh = window.innerHeight;
                                                // 只取页面右侧顶部区域（预览面板操作区）
                                                if (rect.right < vw * 0.7 || rect.top > vh * 0.2) continue;

                                                const cx = rect.left + rect.width / 2;
                                                const cy = rect.top + rect.height / 2;

                                                // 判断可点击性
                                                const style = window.getComputedStyle(el);
                                                const tag = el.tagName.toLowerCase();
                                                const role = el.getAttribute('role');
                                                const isClickable = style.cursor === 'pointer' ||
                                                                    tag === 'button' ||
                                                                    tag === 'a' ||
                                                                    role === 'button' ||
                                                                    el.closest('button, [role="button"], a');

                                                if (!isClickable) continue;

                                                const text = (el.textContent || '').trim();
                                                const isClose = text === '×' || text === 'x' || text === 'X' ||
                                                                text === '✕' || text === '✖' ||
                                                                text.includes('关闭') || text.includes('Close');

                                                candidates.push({
                                                    cx: cx, cy: cy, w: rect.width, h: rect.height,
                                                    score: cx * 2 - cy * 0.5,
                                                    isClose: isClose,
                                                    tag: tag,
                                                    text: text.slice(0, 30),
                                                });
                                            }
                                            candidates.sort((a, b) => b.score - a.score);
                                            return candidates.slice(0, 6);
                                        }""")

                                        logger.info(f"Preview panel top-right candidates: {candidates}")

                                        # 逐一尝试点击候选（优先非关闭按钮）
                                        for idx, c in enumerate(candidates):
                                            if c.get('isClose'):
                                                continue
                                            try:
                                                # 通过 elementFromPoint 找到真正的可点击父元素
                                                # SVG 内部的 click handler 通常在 button/a/role=button 父元素上
                                                click_target = await page.evaluate("""(args) => {
                                                    const el = document.elementFromPoint(args.cx, args.cy);
                                                    if (!el) return null;
                                                    let target = el;
                                                    let depth = 0;
                                                    while (target && target !== document.body && depth < 5) {
                                                        const tag = target.tagName.toLowerCase();
                                                        const role = target.getAttribute('role');
                                                        const style = window.getComputedStyle(target);
                                                        if (tag === 'button' || tag === 'a' || role === 'button' || style.cursor === 'pointer') {
                                                            const rect = target.getBoundingClientRect();
                                                            return {
                                                                cx: rect.left + rect.width / 2,
                                                                cy: rect.top + rect.height / 2,
                                                                tag: tag,
                                                                className: target.className || '',
                                                            };
                                                        }
                                                        target = target.parentElement;
                                                        depth++;
                                                    }
                                                    // 向上遍历5层都没找到标准可点击元素，fallback 到父元素（Vue 组件容器）
                                                    if (el.parentElement && el.parentElement !== document.body) {
                                                        const rect = el.parentElement.getBoundingClientRect();
                                                        return {
                                                            cx: rect.left + rect.width / 2,
                                                            cy: rect.top + rect.height / 2,
                                                            tag: el.parentElement.tagName.toLowerCase(),
                                                            className: el.parentElement.className || '',
                                                        };
                                                    }
                                                    const rect = el.getBoundingClientRect();
                                                    return {
                                                        cx: rect.left + rect.width / 2,
                                                        cy: rect.top + rect.height / 2,
                                                        tag: el.tagName.toLowerCase(),
                                                        className: el.className || '',
                                                    };
                                                }""", {"cx": c['cx'], "cy": c['cy']})

                                                if not click_target:
                                                    logger.warning(f"Candidate {idx}: no element at ({c['cx']:.0f}, {c['cy']:.0f})")
                                                    continue

                                                logger.info(f"Trying mouse.click at ({click_target['cx']:.0f}, {click_target['cy']:.0f}) [{click_target['tag']}] class={click_target.get('className', '')}")
                                                async with page.expect_download(timeout=15000) as download_info:
                                                    await page.mouse.click(click_target['cx'], click_target['cy'])
                                                download = await download_info.value
                                                suggested = download.suggested_filename or original_name
                                                suggested = _sanitize_filename(suggested)
                                                filepath = _resolve_filepath(media_dir, suggested)
                                                await download.save_as(filepath)
                                                actual_name = os.path.basename(filepath)
                                                actual_ext = _ext_from_url(f"/fake.{actual_name}") or "bin"
                                                download_result = {
                                                    "type": _file_type_from_ext(actual_ext),
                                                    "path": f"/media/{actual_name}",
                                                    "filename": actual_name,
                                                    "local_path": filepath,
                                                    "source": url,
                                                }
                                                logger.info(f"Preview-panel downloaded via mouse: {actual_name}")
                                                break
                                            except Exception as e:
                                                logger.warning(f"Candidate {idx} mouse click failed: {e}")
                                                continue
                                    except Exception as e:
                                        logger.warning(f"Coordinate candidate strategy failed: {e}")

                                # ===== 兜底：如果 locator 策略找到了按钮但坐标法没成功，再尝试 locator 点击 =====
                                if not download_result and download_btn and await download_btn.count() > 0:
                                    try:
                                        btn_box = await download_btn.bounding_box()
                                        logger.info(f"Fallback: clicking locator button ({find_reason}) at ~({int(btn_box['x'])},{int(btn_box['y'])})...")
                                    except Exception:
                                        logger.info(f"Fallback: clicking locator button ({find_reason})...")
                                    async with page.expect_download(timeout=30000) as download_info:
                                        try:
                                            await download_btn.click(force=True, timeout=10000)
                                        except Exception as click_err:
                                            logger.warning(f"Locator click failed: {click_err}, trying mouse fallback")
                                            btn_box = await download_btn.bounding_box()
                                            if btn_box:
                                                await page.mouse.click(btn_box['x'] + btn_box['width']/2, btn_box['y'] + btn_box['height']/2)
                                            else:
                                                raise
                                    download = await download_info.value
                                    suggested = download.suggested_filename or original_name
                                    suggested = _sanitize_filename(suggested)
                                    filepath = _resolve_filepath(media_dir, suggested)
                                    await download.save_as(filepath)
                                    actual_name = os.path.basename(filepath)
                                    actual_ext = _ext_from_url(f"/fake.{actual_name}") or "bin"
                                    download_result = {
                                        "type": _file_type_from_ext(actual_ext),
                                        "path": f"/media/{actual_name}",
                                        "filename": actual_name,
                                        "local_path": filepath,
                                        "source": url,
                                    }
                                    logger.info(f"Preview-panel downloaded via locator: {actual_name}")

                                if not download_result:
                                    logger.warning(f"Download button not found in preview panel for {url}")
                                    # 保存调试截图
                                    try:
                                        debug_path = os.path.join(media_dir, f"debug_preview_{int(time.time())}_{uuid.uuid4().hex[:6]}.png")
                                        await page.screenshot(path=debug_path, full_page=False)
                                        logger.info(f"Debug screenshot saved: {debug_path}")
                                    except Exception:
                                        pass
                                    try:
                                        await page.keyboard.press("Escape")
                                    except Exception:
                                        pass
                            else:
                                logger.warning(f"Link element not found for preview download: {url}")
                        except Exception as e:
                            logger.warning(f"Preview panel download failed for {url}: {e}")
                            try:
                                await page.keyboard.press("Escape")
                            except Exception:
                                pass

                    if download_result:
                        media_files.append(download_result)
                    else:
                        self.has_undownloadable_files = True
                        logger.warning(f"Failed to download sandbox file: {url}, marking session for keep")
            else:
                logger.debug("No sandbox references detected")
        except Exception as e:
            logger.warning(f"Sandbox extraction error: {e}")
            # 如果 sandbox 提取阶段本身抛异常，保守起见保留会话
            self.has_undownloadable_files = True

        # ========== 阶段 C：file cards 提取（独立异常隔离）==========
        try:
            file_cards = await page.evaluate(
                """() => {
                    const selectors = ['.chat-content-item-assistant', '.segment-assistant', '.markdown-container'];
                    const results = [];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        if (!els.length) continue;
                        const el = els[els.length - 1];
                        el.querySelectorAll('a, button, [role="button"]').forEach(link => {
                            const text = (link.textContent || '').trim();
                            const href = link.href || '';
                            if (text.match(/\\.(pptx?|pdf|docx?|xlsx?|zip|rar)/i) || text.includes('下载') || text.includes('Download')) {
                                results.push({text, href});
                            }
                        });
                    }
                    return results;
                }"""
            )

            if file_cards:
                self.has_undownloadable_files = True
                logger.info(f"Detected {len(file_cards)} file cards, session will be kept")
        except Exception as e:
            logger.warning(f"File cards extraction error: {e}")

        self.last_media_files = media_files
        return media_files

    # ==================== Cleanup ====================

    async def _cleanup_after_send(self) -> None:
        if self._cleanup_called:
            logger.warning("_cleanup_after_send called twice, skipping")
            return
        self._cleanup_called = True
        if settings.keep_chat:
            logger.info("KEEP_CHAT=true, preserving session")
        elif self.has_undownloadable_files:
            logger.info("Undownloadable files detected, preserving session")
            self.has_undownloadable_files = False
        else:
            await self.delete_current_chat()

    # ==================== Diff ====================

    @staticmethod
    def _diff_text(old: str, new: str) -> str:
        if old == new:
            return ""
        if new.startswith(old):
            return new[len(old) :]
        if not old:
            return new
        idx = new.find(old)
        if idx != -1:
            return new[:idx] + new[idx + len(old) :]
        for i in range(len(old), 0, -1):
            suffix = old[-i:]
            pos = new.find(suffix)
            if pos != -1:
                return new[pos + len(suffix) :]
        return new

    # ==================== Model selection ====================

    async def _select_model(self, model_name: str = None) -> None:
        """
        在 Kimi 页面选择模型模式（K2.6 快速 / 思考 / Agent / Agent 集群）
        通过 JS 动态探测下拉菜单坐标，使用 Playwright mouse.click() 点击
        """
        if not model_name:
            return

        # 标准化 model_name，提取目标关键词
        model_lower = model_name.lower()
        target_keywords = []
        if "agent-cluster" in model_lower or "集群" in model_lower:
            target_keywords = ["Agent 集群", "agent-cluster", "集群"]
        elif "agent" in model_lower:
            target_keywords = ["Agent", "agent"]
        elif "think" in model_lower or "思考" in model_lower:
            target_keywords = ["思考", "think"]
        elif "fast" in model_lower or "快速" in model_lower:
            target_keywords = ["快速", "fast"]
        elif "kimi" in model_lower:
            # 默认切换到 K2.6 快速
            target_keywords = ["快速", "fast"]
        else:
            return  # 无法识别，不操作

        logger.info(f"准备选择 Kimi 模型，目标关键词: {target_keywords}")

        page = self._get_page()
        try:
            # 步骤1: 检查当前是否已经是目标模型（只检查输入框右上方的模型选择按钮）
            check_result = await page.evaluate(
                """(keywords) => {
                    const inputBox = document.querySelector('textarea, div[contenteditable="true"]');
                    const inputRect = inputBox ? inputBox.getBoundingClientRect() : null;
                    const viewportW = window.innerWidth;
                    const all = document.querySelectorAll('*');

                    for (const el of all) {
                        const text = (el.textContent || '').trim();
                        const rect = el.getBoundingClientRect();
                        if (rect.width === 0 || rect.height === 0) continue;
                        if (rect.x < 0 || rect.x > viewportW || rect.y < 0) continue;

                        // 只匹配输入框右上方的小元素（模型选择按钮区域）
                        if (inputRect) {
                            const aboveInput = rect.y >= inputRect.y - 150 && rect.y < inputRect.y - 5;
                            const rightSide = rect.x >= inputRect.x + inputRect.width * 0.3;
                            const smallSize = rect.width > 50 && rect.width < 300 && rect.height > 10 && rect.height < 80;
                            if (!aboveInput || !rightSide || !smallSize) continue;
                        }

                        if (text.includes('K2.6') || text.includes('k2.6')) {
                            for (const kw of keywords) {
                                if (text.includes(kw)) {
                                    return {already: true, text: text};
                                }
                            }
                        }
                    }
                    return {already: false};
                }""",
                target_keywords,
            )

            if check_result.get("already"):
                logger.info(f"当前已是目标模型: {check_result.get('text')}")
                return

            # 步骤2: 获取模型选择按钮的坐标（使用输入框为锚点）
            # 尝试2次，第一次失败后等待页面加载
            btn_coord = None
            for attempt in range(2):
                btn_coord = await page.evaluate(
                    """() => {
                        const inputBox = document.querySelector('textarea, div[contenteditable="true"]');
                        const inputRect = inputBox ? inputBox.getBoundingClientRect() : null;
                        const viewportH = window.innerHeight;
                        const viewportW = window.innerWidth;
                        const all = document.querySelectorAll('*');
                        let best = null;
                        let bestScore = -1;

                        for (const el of all) {
                            const text = (el.textContent || '').trim();
                            const rect = el.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;

                            // 关键过滤：排除侧边栏等视口外元素（x < 0 或 x > viewportW）
                            if (rect.x < 0 || rect.x > viewportW || rect.y < 0 || rect.y > viewportH) continue;

                            // 模型选择器特征：包含 K2.6 或当前模型名称
                            let score = 0;
                            let matched = false;

                            if (text.includes('K2.6') || text.includes('k2.6') || text.includes('快速') || text.includes('思考') || text.includes('Agent')) {
                                if (text.length < 80) matched = true;
                            }

                            if (!matched) continue;

                            // 排除输入框 placeholder / 提示文字（包含"输入""技能"等）
                            if (text.includes('输入') && text.includes('技能')) continue;
                            if (text.includes('placeholder')) continue;

                            if (inputRect) {
                                // 必须在输入框上方 0~200px 范围内，且不能和输入框重叠太多
                                const aboveInput = rect.y >= inputRect.y - 200 && rect.y < inputRect.y - 10;
                                // 必须在输入框右侧区域（模型选择器在右下角）
                                const rightSide = rect.x >= inputRect.x + inputRect.width * 0.4;
                                // 不能离输入框左侧太远
                                const notTooFarLeft = rect.x >= inputRect.x;
                                if (aboveInput) score += 20;
                                if (rightSide) score += 15;
                                if (notTooFarLeft) score += 5;
                            }
                            // 必须在页面底部区域（输入框通常在底部 50%）
                            if (rect.y > viewportH * 0.5) score += 5;
                            // 尺寸合适
                            if (rect.width > 60 && rect.width < 300 && rect.height > 15 && rect.height < 80) score += 5;
                            // 包含 K2.6 精确匹配加分
                            if (text.includes('K2.6') || text.includes('k2.6')) score += 10;
                            // 父元素可点击加分
                            let p = el.parentElement;
                            for (let i = 0; i < 4 && p; i++) {
                                if (p.tagName === 'BUTTON' || p.getAttribute('role') === 'button' || p.onclick) {
                                    score += 8;
                                    break;
                                }
                                p = p.parentElement;
                            }

                            if (score > bestScore) {
                                bestScore = score;
                                best = {x: rect.x, y: rect.y, w: rect.width, h: rect.height, text: text, score: score};
                            }
                        }
                        return best ? {found: true, ...best} : {found: false};
                    }"""
                )

                if btn_coord.get("found"):
                    break
                if attempt == 0:
                    logger.info("模型选择按钮未找到，等待1秒后重试...")
                    await asyncio.sleep(1.0)

            if not btn_coord or not btn_coord.get("found"):
                logger.warning("未找到 Kimi 模型选择按钮坐标，模型可能尚未加载")
                # 调试：输出页面上所有匹配文字的可见元素
                debug_info = await page.evaluate(
                    """() => {
                        const all = document.querySelectorAll('*');
                        const viewportW = window.innerWidth;
                        const viewportH = window.innerHeight;
                        let matches = [];
                        for (const el of all) {
                            const text = (el.textContent || '').trim();
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 && rect.x >= 0 && rect.x <= viewportW && rect.y >= 0 && rect.y <= viewportH) {
                                if ((text.includes('K2.6') || text.includes('k2.6') || text.includes('快速') || text.includes('思考') || text.includes('Agent')) && text.length < 100) {
                                    if (text.includes('输入') && text.includes('技能')) continue;
                                    matches.push({text: text, x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)});
                                }
                            }
                        }
                        return matches.slice(0, 20);
                    }"""
                )
                logger.debug(f"页面上匹配模型文字的可见元素: {debug_info}")
                return

            logger.info(f"模型选择按钮坐标: ({btn_coord['x']:.0f}, {btn_coord['y']:.0f}) 文字: {btn_coord.get('text')} 分数: {btn_coord.get('score')}")

            # 使用 Playwright mouse.click() 点击（更可靠，能穿透 React 事件委托）
            await page.mouse.click(
                btn_coord["x"] + btn_coord["w"] / 2,
                btn_coord["y"] + btn_coord["h"] / 2,
            )
            logger.info("已点击模型选择按钮")
            await asyncio.sleep(1.2)  # 等待下拉菜单展开

            # 步骤3: 获取目标选项的坐标并点击
            option_coord = await page.evaluate(
                """(keywords) => {
                    const all = document.querySelectorAll('*');
                    let best = null;
                    let bestScore = -1;
                    const viewportH = window.innerHeight;

                    for (const el of all) {
                        const text = (el.textContent || '').trim();
                        for (const kw of keywords) {
                            if (text.includes(kw)) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width === 0 || rect.height === 0) continue;

                                // 选项特征：位于页面中下部，宽度适中
                                let score = 0;
                                if (rect.y > viewportH * 0.5) score += 5;
                                if (rect.width > 100 && rect.width < 400) score += 5;
                                if (rect.height > 30 && rect.height < 120) score += 5;
                                // 包含 K2.6 加分
                                if (text.includes('K2.6') || text.includes('k2.6')) score += 10;
                                // 越靠近页面底部（输入框区域）分数越高
                                if (rect.y > viewportH * 0.6) score += 5;

                                if (score > bestScore) {
                                    bestScore = score;
                                    best = {x: rect.x, y: rect.y, w: rect.width, h: rect.height, text: text, score: score};
                                }
                            }
                        }
                    }
                    return best ? {found: true, ...best} : {found: false};
                }""",
                target_keywords,
            )

            if not option_coord.get("found"):
                logger.warning(f"未找到目标模型选项坐标: {target_keywords}")
                # 尝试按 Escape 关闭菜单
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                return

            logger.info(f"目标选项坐标: ({option_coord['x']:.0f}, {option_coord['y']:.0f}) 文字: {option_coord.get('text')} 分数: {option_coord.get('score')}")

            await page.mouse.click(
                option_coord["x"] + option_coord["w"] / 2,
                option_coord["y"] + option_coord["h"] / 2,
            )
            logger.info(f"已选择 Kimi 模型: {option_coord.get('text')}")
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.warning(f"选择 Kimi 模型时出错: {e}")


LLMClientFactory.register("kimi", KimiClient)
LLMClientFactory.register("moonshot", KimiClient)

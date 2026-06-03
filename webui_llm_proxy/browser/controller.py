"""
浏览器控制器 — Playwright 浏览器生命周期管理
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Optional

from playwright.async_api import BrowserContext, Page, async_playwright

from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


def _ignore_locked(src: str, names: list) -> set:
    """忽略 Chrome 运行时会锁定的文件/目录"""
    ignored = {"SingletonLock", "DevToolsActivePort"}
    for name in names:
        if name.endswith(".pma"):
            ignored.add(name)
            continue
        if name in {"LOCK", "LOG", "LOG.old", "CURRENT", "MANIFEST-*"}:
            ignored.add(name)
            continue
        # Chrome SQLite 数据库及相关文件在运行时被锁定
        if name.endswith(("-journal", "-wal", "-shm")):
            ignored.add(name)
            continue
    return ignored


class BrowserController:
    """
    Playwright 浏览器生命周期管理器
    负责浏览器启动、页面管理、资源释放
    """

    def __init__(self) -> None:
        self._playwright: Optional[async_playwright] = None  # type: ignore
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    @staticmethod
    def copy_profile(source_dir: str, target_dir: str) -> None:
        """
        复制 Chrome profile 目录，跳过运行时锁定的文件。
        用于为浏览器实例池创建独立的 profile 副本。

        采用递归复制 + 逐文件错误隔离，确保即使部分文件被锁定，
        其余文件仍能正常复制。
        """
        import errno

        os.makedirs(target_dir, exist_ok=True)

        for root, dirs, files in os.walk(source_dir):
            rel_root = os.path.relpath(root, source_dir)
            dst_root = os.path.join(target_dir, rel_root) if rel_root != "." else target_dir
            os.makedirs(dst_root, exist_ok=True)

            for name in files:
                if name in {"SingletonLock", "DevToolsActivePort"}:
                    continue
                if name.endswith(".pma"):
                    continue
                if name in {"LOCK", "LOG", "LOG.old"}:
                    continue
                if name.endswith(("-journal", "-wal", "-shm")):
                    continue

                s = os.path.join(root, name)
                d = os.path.join(dst_root, name)
                try:
                    shutil.copy2(s, d)
                except (PermissionError, OSError) as e:
                    if isinstance(e, OSError) and e.errno == errno.EACCES:
                        pass
                    logger.debug(f"Skipping locked file: {s}")

            # 清理 dirs 列表以跳过被锁定的子目录（如 Cache）
            dirs[:] = [
                d for d in dirs
                if d not in {"SingletonLock", "DevToolsActivePort"}
            ]

    @staticmethod
    def _prelaunch_profile_check(profile_dir: str) -> None:
        """
        在启动前检查并修复可能的 profile 目录问题。
        """
        # 1. 清理空的 Last Version 文件（会导致 Chrome 启动异常）
        last_version = os.path.join(profile_dir, "Last Version")
        if os.path.exists(last_version) and os.path.getsize(last_version) == 0:
            logger.warning(f"Empty 'Last Version' detected in profile, removing: {last_version}")
            try:
                os.remove(last_version)
            except Exception as e:
                logger.warning(f"Failed to remove empty Last Version: {e}")

        # 2. 清理可能残留的 SingletonLock
        singleton_lock = os.path.join(profile_dir, "SingletonLock")
        if os.path.exists(singleton_lock):
            logger.warning(f"Stale SingletonLock detected, removing: {singleton_lock}")
            try:
                os.remove(singleton_lock)
            except Exception as e:
                logger.warning(f"Failed to remove SingletonLock: {e}")

    async def launch(
        self,
        profile_dir: str,
        channel: str | None = "chrome",
    ) -> BrowserContext:
        """
        启动浏览器（使用本地 Chrome + persistent context）

        Args:
            profile_dir: 用户数据目录路径
            channel: Chrome 通道

        Returns:
            BrowserContext 实例
        """
        logger.info("Starting browser...")

        # 如果浏览器已经在运行且可用，直接复用
        if self._context is not None:
            try:
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
                logger.info("Browser already running, reusing existing context")
                return self._context
            except Exception:
                logger.info("Existing browser context closed, restarting...")
                await self.close()

        self._playwright = await async_playwright().start()

        os.makedirs(profile_dir, exist_ok=True)
        self._prelaunch_profile_check(profile_dir)

        launch_opts: dict = {
            "headless": settings.browser.headless,
            "slow_mo": settings.browser.slow_mo,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--new-window",               # 强制新窗口，防止委托给已有实例
                "--no-default-browser-check", # 不检查默认浏览器
            ],
        }
        if channel is not None:
            launch_opts["channel"] = channel

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                profile_dir,
                **launch_opts,
                viewport=settings.browser.viewport,
            )
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Browser launch failed: {error_msg}")

            # Windows 上常见：Chrome 已运行导致 persistent context 无法启动
            if "has been closed" in error_msg or "Target page" in error_msg:
                logger.warning(
                    "Detected possible Chrome multi-instance conflict on Windows. "
                    "Attempting fallback with temporary profile copy..."
                )
                import tempfile

                temp_dir = tempfile.mkdtemp(prefix="chrome_profile_")
                logger.info(f"Copying profile to temporary dir: {temp_dir}")
                try:
                    BrowserController.copy_profile(profile_dir, temp_dir)
                    self._context = await self._playwright.chromium.launch_persistent_context(
                        temp_dir,
                        **launch_opts,
                        viewport=settings.browser.viewport,
                    )
                    self._fallback_profile_dir = temp_dir
                    logger.info("Browser started with temporary profile fallback")
                except Exception as fallback_e:
                    logger.error(f"Fallback launch also failed: {fallback_e}")
                    raise RuntimeError(
                        f"Browser launch failed. "
                        f"Original error: {error_msg}. "
                        f"This often happens when Chrome is already running on Windows. "
                        f"Please close all Chrome windows and retry, "
                        f"or set PROXY_BROWSER_USE_LOCAL_CHROME=false to use Playwright bundled Chromium."
                    ) from fallback_e
            else:
                raise

        # persistent context 的 browser 属性可能为 None
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()

        logger.info("Browser started")
        return self._context

    async def navigate(self, url: str, max_retries: int = 3) -> None:
        """
        导航到指定 URL（带重试）

        Args:
            url: 目标 URL
            max_retries: 最大重试次数
        """
        logger.info(f"Navigating to: {url}")
        for attempt in range(max_retries):
            try:
                await self._page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                logger.info("Page loaded")
                return
            except Exception as e:
                logger.warning(f"Page load failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    raise

    async def wait_for_login(
        self,
        chat_url: str,
        login_indicator: str = "signin",
        timeout: int = 120,
    ) -> None:
        """
        等待用户完成登录

        Args:
            chat_url: 登录成功后应跳转到的 URL
            login_indicator: URL 中标识登录页面的字符串
            timeout: 等待超时（秒）
        """
        current_url = self._page.url
        if login_indicator not in current_url:
            return

        logger.info("=" * 50)
        logger.info("Login required. Please complete login in the browser window.")
        logger.info(f"Waiting for login, timeout: {timeout}s")
        logger.info("=" * 50)

        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            await asyncio.sleep(2)
            current = self._page.url
            if chat_url in current and login_indicator not in current:
                logger.info("Login successful. Session saved.")
                return

        raise TimeoutError("Login timeout, please check browser status")

    async def screenshot(self, path: str) -> None:
        """截取当前页面"""
        await self._page.screenshot(path=path)
        logger.info(f"Screenshot saved: {path}")

    async def close(self) -> None:
        """关闭浏览器，释放资源"""
        logger.info("Closing browser...")

        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug(f"Error closing playwright: {e}")

        # 清理临时 profile 目录
        fallback_dir = getattr(self, "_fallback_profile_dir", None)
        if fallback_dir and os.path.exists(fallback_dir):
            import shutil
            logger.info(f"Cleaning up temporary profile: {fallback_dir}")
            try:
                shutil.rmtree(fallback_dir, ignore_errors=True)
            except Exception as e:
                logger.warning(f"Failed to cleanup temporary profile: {e}")
            self._fallback_profile_dir = None

        self._page = None
        self._context = None
        self._playwright = None
        logger.info("Browser closed")

    async def __aenter__(self) -> BrowserController:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

"""
LLM Web UI 客户端抽象基类 — 模板方法模式 (Template Method Pattern)

定义了与 Web UI LLM 交互的通用算法骨架：
  start() → 启动浏览器 → 导航 → 登录检测 → 就绪确认
  send_message() → 准备聊天 → 上传文件 → 输入消息 → 点击发送 → 等待完成 → 提取文本 → 清理
  send_message_stream() → 同上，但流式 yield
  close() → 资源释放

子类通过实现钩子方法来定制特定 LLM 的行为。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Callable, Optional

from playwright.async_api import Page

from webui_llm_proxy.browser.controller import BrowserController
from webui_llm_proxy.core.detection_strategies import CompletionDetectionStrategy

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """
    LLM Web UI 客户端抽象基类

    使用模板方法模式定义了与 Web UI LLM 交互的标准流程，
    子类通过实现钩子方法来适配不同的 LLM 平台。
    """

    def __init__(
        self,
        browser: BrowserController,
        detection_strategy: Optional[CompletionDetectionStrategy] = None,
    ) -> None:
        self._browser = browser
        self._detection = detection_strategy
        self._initialized = False
        self.last_media_files: list[dict] = []
        self.has_undownloadable_files: bool = False

    # ==================== 公共属性 ====================

    @property
    def is_ready(self) -> bool:
        return self._initialized

    def _get_page(self) -> Page:
        return self._browser.page

    # ==================== 模板方法：生命周期 ====================

    async def start(self) -> None:
        """
        启动客户端：启动浏览器 → 导航到聊天页面 → 处理登录 → 等待就绪
        """
        logger.info(f"Starting {self.__class__.__name__}...")

        profile_dir = self._get_browser_profile()
        channel = self._get_browser_channel()

        await self._browser.launch(profile_dir=profile_dir, channel=channel)
        await self._browser.navigate(self._get_chat_url())
        await asyncio.sleep(self._get_page_load_wait())

        await self._handle_login()
        await self._wait_for_ready()

        self._initialized = True
        logger.info(f"{self.__class__.__name__} ready")

    async def close(self) -> None:
        """关闭客户端，释放资源"""
        logger.info(f"Closing {self.__class__.__name__}...")
        await self._browser.close()
        self._initialized = False

    async def __aenter__(self) -> BaseLLMClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ==================== 模板方法：非流式发送 ====================

    async def send_message(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
        model_name: Optional[str] = None,
    ) -> str:
        """
        发送消息并等待完整回复（非流式）

        Args:
            message: 用户消息文本
            file_paths: 要上传的本地文件路径列表
            model_name: 模型名称（子类可选使用）

        Returns:
            完整的模型回复文本
        """
        if not self._initialized:
            raise RuntimeError("客户端未初始化，请先调用 start()")

        self._reset_state()
        self._detection.reset() if self._detection else None

        await self._prepare_chat()

        if model_name:
            await self._select_model(model_name)

        if file_paths:
            upload_ok = await self._upload_files_impl(file_paths)
            if not upload_ok:
                logger.warning("File upload failed, continuing with text message")

        await self._input_message_impl(message)
        await self._click_send_impl()

        response_text = await self._wait_for_complete_response()

        await self._extract_media_files()
        await self._cleanup_after_send()

        logger.info(f"Response complete, length: {len(response_text)}")
        return response_text

    # ==================== 模板方法：流式发送 ====================

    async def send_message_stream(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
        model_name: Optional[str] = None,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        发送消息并以流式方式获取回复

        Args:
            message: 用户消息文本
            file_paths: 要上传的本地文件路径列表
            model_name: 模型名称
            on_chunk: 每收到一块内容时的回调函数

        Yields:
            逐字/逐句的文本片段
        """
        if not self._initialized:
            raise RuntimeError("客户端未初始化，请先调用 start()")

        self._reset_state()
        self._detection.reset() if self._detection else None

        await self._prepare_chat()

        if model_name:
            await self._select_model(model_name)

        if file_paths:
            upload_ok = await self._upload_files_impl(file_paths)
            if not upload_ok:
                logger.warning("File upload failed, continuing with text message")

        await self._input_message_impl(message)
        await self._click_send_impl()

        try:
            async for chunk in self._stream_response(on_chunk):
                yield chunk
        finally:
            await self._extract_media_files()
            await self._cleanup_after_send()

    # ==================== 抽象钩子：必须实现 ====================

    @abstractmethod
    def _get_chat_url(self) -> str:
        """返回 LLM Web UI 的聊天页面 URL"""
        ...

    @abstractmethod
    def _get_browser_profile(self) -> str:
        """返回浏览器用户数据目录路径"""
        ...

    @abstractmethod
    async def _upload_files_impl(self, file_paths: list[str]) -> bool:
        """上传文件的具体实现"""
        ...

    @abstractmethod
    async def _extract_response_text(self, skip_count: int = 0) -> str:
        """从页面提取最后一条模型回复的文本"""
        ...

    # ==================== 可选钩子：有默认实现 ====================

    def _get_browser_channel(self) -> str | None:
        """返回 Chrome 通道，默认 'chrome'（本地 Chrome）。若配置不使用本地 Chrome，则返回 None 使用 Playwright 自带 Chromium。"""
        from webui_llm_proxy.config import settings
        if not settings.browser.use_local_chrome:
            return None
        return settings.browser.chrome_channel

    def _get_page_load_wait(self) -> int:
        """页面加载后的等待时间（秒）"""
        return 2

    def _get_response_start_timeout(self) -> int:
        """等待回复开始的超时时间（秒）"""
        return 60

    def _get_stream_idle_timeout(self) -> int:
        """流式输出空闲超时（秒）"""
        return 180

    def _get_poll_interval(self) -> float:
        """DOM 轮询间隔（秒）"""
        return 1.0

    def _reset_state(self) -> None:
        """重置每次请求的状态"""
        self.last_media_files = []
        self.has_undownloadable_files = False

    async def _handle_login(self) -> None:
        """处理登录逻辑，默认检查 URL 是否包含 login"""
        from webui_llm_proxy.config import settings
        current_url = self._get_page().url
        if "login" in current_url or "signin" in current_url or "accounts.google" in current_url:
            await self._browser.wait_for_login(
                chat_url=self._get_chat_url(),
                timeout=settings.browser.headless and 120 or 300,
            )

    async def _wait_for_ready(self) -> None:
        """等待页面就绪，默认等待固定时间"""
        await asyncio.sleep(self._get_page_load_wait())

    async def _prepare_chat(self) -> None:
        """准备新对话，默认调用 new_chat()"""
        await self.new_chat()

    async def _input_message_impl(self, message: str) -> None:
        """
        Input message into the chat input box.
        Tries selectors in order, prefers editable elements.
        Falls back to click + type for non-standard inputs.
        """
        page = self._get_page()
        selectors = self._get_input_selectors()

        input_box = None
        for sel in selectors:
            try:
                locator = page.locator(sel).first
                if await locator.count() == 0:
                    continue
                # Prefer truly editable elements
                try:
                    if await locator.is_editable(timeout=2000):
                        input_box = locator
                        break
                except Exception:
                    pass
                # If not obviously editable, check if it's contenteditable
                try:
                    editable = await locator.evaluate(
                        "el => el.isContentEditable || el.getAttribute('contenteditable') === 'true'"
                    )
                    if editable:
                        input_box = locator
                        break
                except Exception:
                    pass
            except Exception:
                continue

        if input_box is None:
            raise RuntimeError(f"No editable input found, tried selectors: {selectors}")

        try:
            await input_box.wait_for(state="visible", timeout=10000)
        except Exception:
            await input_box.wait_for(state="attached", timeout=10000)
            await input_box.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)

        # Try fill first, fall back to click + type for custom inputs
        try:
            await input_box.fill(message)
        except Exception:
            await input_box.click()
            await asyncio.sleep(0.3)
            await input_box.press("Control+a")
            await input_box.press("Delete")
            await input_box.type(message, delay=10)

        await asyncio.sleep(0.3)

    async def _click_send_impl(self) -> None:
        """
        点击发送按钮
        默认实现：尝试多个选择器，失败则按 Enter
        """
        page = self._get_page()
        selectors = self._get_send_button_selectors()

        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    logger.debug(f"Send button clicked: {sel}")
                    return
            except Exception:
                continue

        # 回退：按 Enter
        logger.debug("Trying Enter key to send")
        input_selectors = self._get_input_selectors()
        for sel in input_selectors:
            try:
                box = page.locator(sel).first
                if await box.count() > 0:
                    await box.press("Enter")
                    await asyncio.sleep(0.5)
                    await box.press("Enter")
                    return
            except Exception:
                continue

    async def _select_model(self, model_name: str) -> None:
        """
        选择模型（可选钩子）
        默认空实现，Kimi 子类重写
        """
        pass

    async def _wait_for_complete_response(self) -> str:
        """
        等待完整回复（非流式）
        使用注入的检测策略判断完成
        """
        last_text = ""
        response_start = asyncio.get_event_loop().time()

        # 等待回复开始
        while True:
            await asyncio.sleep(self._get_poll_interval())
            current_text = await self._extract_response_text()
            if current_text and current_text != last_text:
                logger.debug("Response started")
                last_text = current_text
                break
            if asyncio.get_event_loop().time() - response_start > self._get_response_start_timeout():
                logger.warning("Response start timeout")
                break

        # 等待回复完成
        if self._detection is not None:
            while not await self._detection.is_complete(self):
                await asyncio.sleep(self._get_poll_interval())
            last_text = await self._extract_response_text()
        else:
            # 无策略时的默认行为：稳定计数
            idle_start = asyncio.get_event_loop().time()
            stable_count = 0
            while stable_count < 3:
                await asyncio.sleep(self._get_poll_interval())
                current_text = await self._extract_response_text()
                if current_text == last_text:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_text = current_text
                    idle_start = asyncio.get_event_loop().time()
                if asyncio.get_event_loop().time() - idle_start > self._get_stream_idle_timeout():
                    logger.debug("Idle timeout, response complete")
                    break

        return last_text

    async def _stream_response(
        self,
        on_chunk: Optional[Callable[[str], None]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式捕获回复
        """
        last_text = ""
        idle_start = asyncio.get_event_loop().time()
        started = False

        while True:
            await asyncio.sleep(self._get_poll_interval())
            current_text = await self._extract_response_text()

            if not current_text:
                if not started and asyncio.get_event_loop().time() - idle_start > self._get_response_start_timeout():
                    logger.warning("Stream response start timeout")
                    break
                continue

            if not started and current_text:
                started = True
                logger.debug("Stream response started")

            # 使用策略检测完成
            if self._detection is not None:
                if await self._detection.is_complete(self):
                    new_content = self._diff_text(last_text, current_text)
                    if new_content:
                        yield new_content
                        if on_chunk:
                            on_chunk(new_content)
                    break

            if current_text != last_text:
                new_content = self._diff_text(last_text, current_text)
                if new_content:
                    yield new_content
                    if on_chunk:
                        on_chunk(new_content)
                last_text = current_text
                idle_start = asyncio.get_event_loop().time()
            else:
                # 无策略时的默认空闲检测
                if self._detection is None:
                    if asyncio.get_event_loop().time() - idle_start > self._get_stream_idle_timeout():
                        logger.debug("Stream idle timeout")
                        break

        logger.info(f"[Stream] Response complete, total length: {len(last_text)}")

    async def _extract_media_files(self) -> None:
        """
        提取媒体文件（可选钩子）
        默认空实现，Kimi 子类重写
        """
        pass

    async def _cleanup_after_send(self) -> None:
        """
        发送后的清理工作（可选钩子）
        默认空实现，Kimi 子类重写（如删除会话）
        """
        pass

    # ==================== 辅助方法 ====================

    @staticmethod
    def _diff_text(old: str, new: str) -> str:
        """计算文本差异，返回新增部分"""
        if old == new:
            return ""
        if new.startswith(old):
            return new[len(old):]
        return new

    async def new_chat(self) -> None:
        """
        开始新对话（可选钩子）
        默认不执行任何操作，子类可重写
        """
        pass

    async def screenshot(self, path: str) -> None:
        """截取当前页面"""
        await self._browser.screenshot(path)

    # ==================== 选择器（子类可重写）====================

    def _get_input_selectors(self) -> list[str]:
        """返回输入框选择器列表"""
        return ["textarea", 'div[contenteditable="true"]']

    def _get_send_button_selectors(self) -> list[str]:
        """返回发送按钮选择器列表"""
        return [
            'button[type="submit"]',
            'button[aria-label*="发送"]',
            'button[aria-label*="Send"]',
        ]

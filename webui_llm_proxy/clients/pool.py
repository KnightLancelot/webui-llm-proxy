"""
LLM 客户端池 — 支持多实例并发

为每个模型前缀维护 N 个独立的浏览器实例，
请求通过 acquire/release 从池中 checkout/checkin。
"""

from __future__ import annotations

import asyncio
import logging
import os

from webui_llm_proxy.browser.controller import BrowserController
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.clients.factory import LLMClientFactory
from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


class ClientPool:
    """
    每个模型对应一个客户端池，内部维护 N 个独立的浏览器实例。

    Usage:
        pool = ClientPool("kimi", size=3)
        await pool.initialize()
        client = await pool.acquire()
        try:
            response = await client.send_message("...")
        finally:
            await pool.release(client)
    """

    def __init__(self, model_prefix: str, size: int) -> None:
        self.model_prefix = model_prefix
        self.size = size
        self.queue: asyncio.Queue[BaseLLMClient] = asyncio.Queue(maxsize=size)
        self.clients: list[BaseLLMClient] = []
        # 记录每个客户端实例对应的 profile 目录
        self._client_profiles: dict[int, str] = {}

    def _get_base_profile(self) -> str:
        """返回该模型对应的原始 profile 目录。"""
        if self.model_prefix in ("kimi", "moonshot"):
            return settings.browser.kimi_user_data_dir
        return settings.browser.user_data_dir

    async def initialize(self) -> None:
        """
        初始化池中所有客户端实例。

        实例 0 直接使用原始 profile；
        实例 1~N-1 复制原始 profile 到独立目录（保留登录态）。

        策略：先为所有实例准备好 profile 目录（复制），再逐个启动 Chrome，
        避免实例 0 启动后锁定 profile 导致后续复制失败。
        """
        base_profile = self._get_base_profile()
        logger.info(f"Initializing client pool for '{self.model_prefix}' (size={self.size})...")

        # ---------- 阶段 1：准备所有 profile 目录（Chrome 还未启动） ----------
        profile_dirs: list[str] = []
        for i in range(self.size):
            if i == 0:
                profile_dir = base_profile
            else:
                profile_dir = f"{base_profile}_pool_{i}"
                if not os.path.exists(profile_dir):
                    logger.info(f"Copying base profile to {profile_dir} for pool instance {i}")
                    try:
                        BrowserController.copy_profile(base_profile, profile_dir)
                        logger.info(f"Profile copied for instance {i}")
                    except Exception as e:
                        logger.warning(f"Failed to copy profile for instance {i}: {e}. "
                                       f"Using empty profile (may require re-login).")
                        os.makedirs(profile_dir, exist_ok=True)
            profile_dirs.append(profile_dir)

        # ---------- 阶段 2：逐个启动 Chrome 实例 ----------
        for i, profile_dir in enumerate(profile_dirs):
            browser = BrowserController()
            client = LLMClientFactory.create(self.model_prefix, browser)
            self._client_profiles[id(client)] = profile_dir

            # 调用底层启动（绕过 client.start() 中的 navigate + login check，
            # 因为我们只需要浏览器起来，页面状态由具体请求控制）
            try:
                await client._browser.launch(profile_dir=profile_dir, channel=client._get_browser_channel())
                await client._browser.navigate(client._get_chat_url())
                await asyncio.sleep(client._get_page_load_wait())
                await client._handle_login()
                await client._wait_for_ready()
                client._initialized = True
                logger.info(f"Pool instance {i} for '{self.model_prefix}' ready")
            except Exception as e:
                logger.error(f"Failed to start pool instance {i} for '{self.model_prefix}': {e}")
                # 即使启动失败也把实例放进去，避免队列永远不满导致请求卡死
                # 请求时 acquire() 会再次尝试启动

            self.clients.append(client)
            await self.queue.put(client)

        ready_count = sum(1 for c in self.clients if c.is_ready)
        logger.info(f"Client pool for '{self.model_prefix}' initialized "
                    f"({ready_count}/{len(self.clients)} ready)")

    async def _restart_client(self, client: BaseLLMClient) -> None:
        """使用实例对应的 profile_dir 重新启动客户端。"""
        profile_dir = self._client_profiles.get(id(client))
        if not profile_dir:
            raise RuntimeError("No profile directory recorded for this client instance")

        logger.warning(f"Restarting client with profile: {profile_dir}")
        await client._browser.launch(profile_dir=profile_dir, channel=client._get_browser_channel())
        await client._browser.navigate(client._get_chat_url())
        await asyncio.sleep(client._get_page_load_wait())
        await client._handle_login()
        await client._wait_for_ready()
        client._initialized = True
        logger.info(f"Client restarted successfully")

    async def acquire(self) -> BaseLLMClient:
        """从池中获取一个可用客户端（阻塞等待）。

        如果取出的客户端未初始化（如启动时失败），会自动尝试重新启动。
        """
        client = await self.queue.get()

        if not client.is_ready:
            logger.warning(f"Acquired client not ready, attempting restart...")
            try:
                await self._restart_client(client)
            except Exception as e:
                logger.error(f"Failed to restart client: {e}")
                # 归还失败的实例，避免队列永久损失一个位置
                await self.queue.put(client)
                raise RuntimeError(
                    f"All pool instances for '{self.model_prefix}' are unavailable. "
                    f"Please check browser logs."
                )

        logger.debug(f"Acquired client from '{self.model_prefix}' pool (remaining: {self.queue.qsize()})")
        return client

    async def release(self, client: BaseLLMClient) -> None:
        """将客户端归还池中，并尝试清理页面状态。"""
        try:
            # 尽量重置到干净的新对话状态，避免下一个请求看到历史
            if client.is_ready:
                await client.new_chat()
        except Exception as e:
            logger.debug(f"new_chat cleanup failed on release: {e}")
        finally:
            await self.queue.put(client)
            logger.debug(f"Released client back to '{self.model_prefix}' pool (available: {self.queue.qsize()})")

    async def close(self) -> None:
        """关闭池中所有客户端并释放资源。"""
        logger.info(f"Closing client pool for '{self.model_prefix}'...")
        for idx, client in enumerate(self.clients):
            try:
                await client.close()
                logger.info(f"Closed pool instance {idx} for '{self.model_prefix}'")
            except Exception as e:
                logger.warning(f"Error closing pool instance {idx}: {e}")

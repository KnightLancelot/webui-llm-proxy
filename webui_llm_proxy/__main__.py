"""
包入口: python -m webui_llm_proxy

支持直接运行 uvicorn 或通过 CLI 管理守护进程。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

import uvicorn

from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


def main() -> int:
    """主入口函数"""
    parser = argparse.ArgumentParser(
        prog="webui-llm-proxy",
        description="WebUI LLM Proxy Server",
    )
    parser.add_argument(
        "--host",
        default=settings.openai.host,
        help=f"Server host (default: {settings.openai.host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.openai.port,
        help=f"Server port (default: {settings.openai.port})",
    )
    parser.add_argument(
        "--keep-chat",
        action="store_true",
        help="Keep chat sessions after completion",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Development mode: auto-reload on code changes",
    )
    parser.add_argument(
        "--models",
        default=settings.enabled_models,
        help=f"Enabled model proxies, comma-separated (default: {settings.enabled_models})",
    )

    args = parser.parse_args()

    if args.keep_chat:
        os.environ["PROXY_KEEP_CHAT"] = "true"
        settings.keep_chat = True
        logger.info("Flag --keep-chat: sessions will be preserved")

    if args.models:
        models_value = args.models.strip('"').strip("'")
        os.environ["PROXY_ENABLED_MODELS"] = models_value
        settings.enabled_models = models_value
        logger.info(f"Enabled models: {settings.enabled_model_list}")

    logger.info(f"Starting server: http://{args.host}:{args.port}")
    logger.info(f"API docs: http://{args.host}:{args.port}/docs")

    uvicorn.run(
        "webui_llm_proxy.api.server:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

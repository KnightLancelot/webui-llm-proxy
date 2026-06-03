"""
DOM 选择器调试工具

打开浏览器，探测页面上的关键元素选择器，
帮助排查输入框、发送按钮、回复区域等选择器配置问题。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys

from playwright.async_api import async_playwright

from webui_llm_proxy.config import settings

logger = logging.getLogger(__name__)


async def probe_selectors(url: str, selectors: dict[str, str]) -> dict:
    """探测页面上的选择器"""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    page = await browser.new_page()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)

        results = {}
        for name, selector in selectors.items():
            try:
                count = await page.locator(selector).count()
                visible = False
                if count > 0:
                    try:
                        visible = await page.locator(selector).first.is_visible(timeout=2000)
                    except Exception:
                        pass
                results[name] = {
                    "selector": selector,
                    "count": count,
                    "visible": visible,
                    "status": "ok" if count > 0 else "not found",
                }
            except Exception as e:
                results[name] = {
                    "selector": selector,
                    "count": 0,
                    "visible": False,
                    "status": f"error: {e}",
                }

        # 额外探测常见选择器
        common_selectors = [
            ("textarea", "textarea"),
            ("contenteditable", 'div[contenteditable="true"]'),
            ("send_button", "button[type=\"submit\"]"),
            ("send_button_aria", 'button[aria-label*="发送"], button[aria-label*="Send"]'),
        ]
        for name, sel in common_selectors:
            if name not in results:
                try:
                    count = await page.locator(sel).count()
                    results[name] = {"selector": sel, "count": count, "status": "probed"}
                except Exception:
                    pass

        await page.screenshot(path="data/debug_screenshot.png")
        results["screenshot"] = "data/debug_screenshot.png"

        return results

    finally:
        await browser.close()
        await pw.stop()


def main() -> int:
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="DOM Selector Debugger")
    parser.add_argument("--backend", choices=["gemini", "kimi"], default="kimi", help="Target backend")
    args = parser.parse_args()

    if args.backend == "gemini":
        url = settings.gemini.chat_url
        selectors = {
            "input_box": settings.gemini.input_selector,
            "send_button": settings.gemini.send_selector,
            "response": settings.gemini.response_selector,
        }
    else:
        url = settings.kimi.chat_url
        selectors = {
            "input_box": settings.kimi.input_selector,
            "send_button": settings.kimi.send_selector,
            "response": settings.kimi.response_selector,
        }

    print(f"Probing {args.backend} at {url}...")
    results = asyncio.run(probe_selectors(url, selectors))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
调试路由 — 截图、编码测试等
"""

import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from webui_llm_proxy.api.dependencies import verify_api_key
from webui_llm_proxy.api.dependencies import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/v1/test/encoding")
async def test_encoding(
    message: str = Form(...),
    _: bool = Depends(verify_api_key),
):
    """
    编码测试接口：只接收 message，不操作浏览器，直接返回原始字节分析
    """
    from webui_llm_proxy.api.dependencies import fix_encoding

    result = {
        "received": message,
        "repr": repr(message),
        "length": len(message),
        "byte_values": [hex(ord(c)) for c in message],
        "utf8_bytes": message.encode("utf-8").hex(),
        "latin1_bytes": message.encode("latin-1", errors="replace").hex(),
        "has_cjk": bool(re.search(r"[\u4e00-\u9fff]", message)),
        "fixed": fix_encoding(message),
        "fixed_repr": repr(fix_encoding(message)),
    }
    return result


@router.post("/v1/gemini/screenshot")
async def take_gemini_screenshot(
    request: Request,
    _: bool = Depends(verify_api_key),
):
    """截取当前 Gemini 页面"""
    enabled_models: list[str] = request.app.state.enabled_models
    if "gemini" not in enabled_models:
        raise HTTPException(status_code=400, detail="Gemini is not enabled")
    pool = get_pool(request, "gemini")
    client = await pool.acquire()
    try:
        if not client.is_ready:
            raise HTTPException(status_code=503, detail="Gemini not ready")
        path = "data/gemini_debug_screenshot.png"
        await client.screenshot(path)
        return {"status": "ok", "screenshot": path}
    finally:
        await pool.release(client)


@router.post("/v1/kimi/screenshot")
async def take_kimi_screenshot(
    request: Request,
    _: bool = Depends(verify_api_key),
):
    """截取当前 Kimi 页面"""
    enabled_models: list[str] = request.app.state.enabled_models
    if "kimi" not in enabled_models:
        raise HTTPException(status_code=400, detail="Kimi is not enabled")
    pool = get_pool(request, "kimi")
    client = await pool.acquire()
    try:
        if not client.is_ready:
            raise HTTPException(status_code=503, detail="Kimi not ready")
        path = "data/kimi_debug_screenshot.png"
        await client.screenshot(path)
        return {"status": "ok", "screenshot": path}
    finally:
        await pool.release(client)

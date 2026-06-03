"""
记忆管理路由 — /v1/clear_memory, /v1/memory/status
"""

from fastapi import APIRouter, Depends, Request

from webui_llm_proxy.api.dependencies import verify_api_key
from webui_llm_proxy.api.server import get_memory

router = APIRouter()


@router.post("/v1/clear_memory")
async def clear_memory(
    request: Request,
    _: bool = Depends(verify_api_key),
):
    """清空当前对话记忆"""
    memory = get_memory(request)
    memory.clear_short_term()
    return {"status": "ok", "message": "Short-term memory cleared"}


@router.get("/v1/memory/status")
async def memory_status(
    request: Request,
    _: bool = Depends(verify_api_key),
):
    """查看当前记忆状态"""
    memory = get_memory(request)
    return memory.get_status()

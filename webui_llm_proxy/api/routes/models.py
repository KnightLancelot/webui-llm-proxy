"""
模型列表路由 — /v1/models
"""

from fastapi import APIRouter, Depends, Request

from webui_llm_proxy.api.dependencies import verify_api_key

router = APIRouter()

# 所有可用模型定义
_ALL_MODELS = [
    {"id": "gemini-pro-via-proxy", "object": "model", "created": 1700000000, "owned_by": "webui-llm-proxy"},
    {"id": "kimi-via-proxy", "object": "model", "created": 1700000001, "owned_by": "webui-llm-proxy"},
    {"id": "kimi-k2.6-fast", "object": "model", "created": 1700000002, "owned_by": "webui-llm-proxy"},
    {"id": "kimi-k2.6-think", "object": "model", "created": 1700000003, "owned_by": "webui-llm-proxy"},
    {"id": "kimi-k2.6-agent", "object": "model", "created": 1700000004, "owned_by": "webui-llm-proxy"},
    {"id": "kimi-k2.6-agent-cluster", "object": "model", "created": 1700000005, "owned_by": "webui-llm-proxy"},
]

# 模型 ID 到前缀映射
_MODEL_PREFIX_MAP = {
    "gemini-pro-via-proxy": "gemini",
    "kimi-via-proxy": "kimi",
    "kimi-k2.6-fast": "kimi",
    "kimi-k2.6-think": "kimi",
    "kimi-k2.6-agent": "kimi",
    "kimi-k2.6-agent-cluster": "kimi",
}


@router.get("/v1/models")
async def list_models(request: Request, _: bool = Depends(verify_api_key)):
    """OpenAI compatible: list available models (filtered by enabled_models)"""
    enabled_models: list[str] = request.app.state.enabled_models

    filtered = [
        m for m in _ALL_MODELS
        if _MODEL_PREFIX_MAP.get(m["id"], "") in enabled_models
    ]

    return {
        "object": "list",
        "data": filtered,
    }

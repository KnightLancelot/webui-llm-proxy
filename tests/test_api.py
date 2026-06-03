"""
API 层测试
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webui_llm_proxy.api.server import create_app


@pytest.fixture
def client():
    """创建测试客户端"""
    app = create_app()

    # 手动初始化 lifespan 中的 state（TestClient 不支持 lifespan 参数时）
    from webui_llm_proxy.core.event_bus import EventBus
    from webui_llm_proxy.core.memory import MemoryManager

    app.state.client_pools = {}
    app.state.event_bus = EventBus()
    app.state.memory = MemoryManager()
    app.state.enabled_models = ["gemini", "kimi"]

    # 测试中临时禁用 API Key 校验，避免依赖外部 .env 配置
    import webui_llm_proxy.config as _config
    original_key = _config.settings.openai.api_key
    _config.settings.openai.api_key = ""
    yield TestClient(app)
    _config.settings.openai.api_key = original_key


class TestRootEndpoint:
    """测试根路由"""

    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "WebUI LLM Proxy" in data["message"]
        assert data["version"] == "3.0.0"


class TestModelsEndpoint:
    """测试模型列表路由"""

    def test_list_models(self, client):
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) >= 5
        model_ids = [m["id"] for m in data["data"]]
        assert "kimi-k2.6-fast" in model_ids
        assert "gemini-pro-via-proxy" in model_ids


class TestMemoryEndpoints:
    """测试记忆管理路由"""

    def test_clear_memory(self, client):
        response = client.post("/v1/clear_memory")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_memory_status(self, client):
        response = client.get("/v1/memory/status")
        assert response.status_code == 200
        data = response.json()
        assert "short_term_count" in data
        assert "long_term_count" in data
        assert "recent_messages" in data


class TestEncodingEndpoint:
    """测试编码修复路由"""

    def test_encoding_fix(self, client):
        response = client.post(
            "/v1/test/encoding",
            data={"message": "\u4f60\u597d"},  # "你好" 的 Unicode
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_cjk"] is True
        assert data["fixed"] == "\u4f60\u597d"

# WebUI LLM Proxy

一个 Web UI LLM 代理服务器，通过 Playwright 控制 Kimi、Gemini 等网页版 LLM，对外暴露 **OpenAI 兼容的 `/v1/chat/completions` API**。

---

## 核心功能

| 特性 | 说明 |
|------|------|
| **多模型后端** | 支持 Kimi（moonshot）和 Gemini Web UI，通过 `model` 参数自动路由 |
| **OpenAI 兼容 API** | 提供 `/v1/chat/completions`、`/v1/models` 等标准接口 |
| **浏览器实例池** | 每个模型可配置 N 个独立 Chrome 实例（`PROXY_BROWSER_POOL_SIZE`），支持并发请求 |
| **流式/非流式输出** | `stream=true/false` 自由切换，流式返回 SSE 事件流 |
| **多模态输入** | 支持图片 URL（`image_url`）和文件上传（`/v1/chat/completions/upload`） |
| **Kimi 模型自动切换** | 根据 `model` 名称关键词自动选择快速/思考/Agent/Agent集群模式 |
| **API Key 认证** | Bearer Token 校验，支持逗号分隔多个 Key |

---

## 项目结构

```
webui_llm_proxy/
├── webui_llm_proxy/           # 主 Python 包
│   ├── config.py              # Pydantic Settings（单例 + 类型安全）
│   ├── api/                   # FastAPI 层（DI + 装饰器模式）
│   │   ├── server.py          # FastAPI 应用 + lifespan
│   │   ├── dependencies.py    # 认证、编码修复等依赖
│   │   └── routes/            # 路由模块
│   ├── adapters/              # 适配器模式
│   │   ├── base.py            # RequestAdapter / ResponseAdapter 接口
│   │   ├── models.py          # ChatRequest / ChatResponse 数据类
│   │   └── openai.py          # OpenAI 格式适配器
│   ├── clients/               # LLM 客户端（模板方法 + 工厂 + 对象池）
│   │   ├── base.py            # BaseLLMClient 抽象基类
│   │   ├── factory.py         # LLMClientFactory 抽象工厂
│   │   ├── pool.py            # ClientPool（对象池模式，支持并发）
│   │   ├── gemini.py          # Gemini 客户端
│   │   └── kimi.py            # Kimi 客户端
│   ├── browser/               # 浏览器抽象
│   │   └── controller.py      # BrowserController（Playwright 封装）
│   ├── core/                  # 核心业务逻辑
│   │   ├── memory.py          # MemoryManager（单例）
│   │   ├── event_bus.py       # EventBus + EventObserver（观察者模式）
│   │   ├── detection_strategies.py  # CompletionDetectionStrategy（策略模式）
│   │   ├── media_extractor.py # 媒体文件提取观察者
│   │   └── usage_logger.py    # 使用台账记录观察者
│   └── cli/                   # 命令行工具
│       ├── server.py          # start/stop/restart/status/logs
│       └── debug_selectors.py # DOM 选择器调试
├── data/                      # 运行时数据（.gitignore）
│   ├── uploads/               # 临时上传文件
│   ├── media/                 # 提取的媒体文件（静态服务 /media）
│   ├── logs/                  # 使用台账日志
│   └── memory.json            # 长期记忆
├── tests/                     # 单元测试（pytest）
├── scripts/                   # Windows 启动脚本
│   ├── start.bat
│   ├── stop.bat
│   └── status.bat
├── .env.example               # 环境变量模板
├── pyproject.toml             # 现代 Python 项目配置
└── README.md
```

---

## 安装

### 环境要求

- Python >= 3.10
- Windows / Linux / macOS
- Google Chrome（推荐，可避免自动化检测）

### 安装步骤

```bash
# 1. 克隆项目
git clone <repo-url>
cd webui_llm_proxy

# 2. 创建虚拟环境
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/macOS

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 安装 Playwright 浏览器
playwright install chromium
# 注意：最好直接用chrome浏览器——gemini不允许在chromium中进行登录。

# 5. 复制环境变量模板
copy .env.example .env
# 按需编辑 .env 文件
```

---

## 使用方法

### 方式 1：直接运行（开发）

```bash
python -m webui_llm_proxy
# 或
python -m webui_llm_proxy --port 8080 --keep-chat
```

### 方式 2：Windows 脚本

```bash
scripts\start.bat           # 启动
scripts\start.bat --keep-chat  # 保留会话模式
scripts\stop.bat            # 停止
scripts\status.bat          # 查看状态
```

### 方式 3：CLI 守护进程

```bash
python -m webui_llm_proxy.cli.server start
python -m webui_llm_proxy.cli.server stop
python -m webui_llm_proxy.cli.server restart --keep-chat
python -m webui_llm_proxy.cli.server status
python -m webui_llm_proxy.cli.server logs
```

---

## 支持的模型

| 平台 | `model` 名称前缀 | 说明 |
|------|-----------------|------|
| **Kimi** | `kimi` | Kimi K2.6 系列，支持通过名称自动切换快速/思考/Agent 模式 |
| **Kimi** | `moonshot` | Moonshot 官方名称，同样映射到 Kimi 客户端 |
| **Gemini** | `gemini` | Google Gemini Web UI |

> **默认 fallback**：如果传入的 `model` 名称未匹配到任何已知前缀，默认使用 **Gemini** 客户端。

### Kimi 模型模式映射

Kimi 客户端会根据 `model` 名称中的关键词自动在网页上切换对应模型：

| `model` 名称关键词 | 实际选择的 Kimi 模型 |
|-------------------|---------------------|
| 包含 `think` / `思考` | **K2.6 思考** |
| 包含 `fast` / `快速` | **K2.6 快速** |
| 包含 `agent-cluster` / `集群` | **Agent 集群** |
| 包含 `agent` | **Agent** |
| 仅包含 `kimi`（无上述关键词） | **K2.6 快速**（默认） |

示例：
- `"model": "kimi-k2.6-think"` → 自动切换为 **K2.6 思考**
- `"model": "kimi"` → 使用 **K2.6 快速**

## API Key 认证

服务支持 **Bearer Token** 认证，所有接口（`/v1/chat/completions`、`/v1/chat/completions/upload`、`/models` 等）默认受保护。

### 配置方式

编辑项目根目录 `.env` 文件：

```env
# 单个 key
PROXY_API_KEY=sk-your-secret-key

# 或多个 key（逗号分隔，方便团队协作）
PROXY_API_KEY=sk-alice-key,sk-bob-key,sk-carol-key
```

> **注意**：`.env` 修改后**必须重启服务**才能生效。未配置 `PROXY_API_KEY` 时，服务不校验 token（方便本地开发）。

### 客户端调用

在请求头中加入 `Authorization: Bearer <your-token>`：

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-secret-key" \
  -d '{"model":"kimi-k2.6-think","messages":[{"role":"user","content":"你好"}]}'
```

Token 错误或缺失时返回：
```json
{"detail":"Invalid API Key"}
```

## API 使用示例

### 非流式请求

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-secret-key" \
  -d '{
    "model": "kimi-k2.6-think",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

### 流式请求

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-secret-key" \
  -d '{
    "model": "kimi-k2.6-think",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

### 多模态请求（图片 URL）

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-your-secret-key" \
  -d '{
    "model": "kimi-k2.6-fast",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
      ]
    }]
  }'
```

### 文件上传

```bash
curl http://localhost:8080/v1/chat/completions/upload \
  -H "Authorization: Bearer sk-your-secret-key" \
  -F "message=总结这个文档" \
  -F "files=@document.pdf" \
  -F "model=kimi-k2.6-fast"
```

### Python OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="sk-your-secret-key"  # 配置 PROXY_API_KEY 后必须填写
)

response = client.chat.completions.create(
    model="kimi-k2.6-fast",
    messages=[{"role": "user", "content": "你好"}],
    stream=False,
)
print(response.choices[0].message.content)
```

---

## 配置说明

所有配置通过 **环境变量** 或 **`.env` 文件** 覆盖，前缀为 `PROXY_`。

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PROXY_HOST` | `0.0.0.0` | 服务监听地址 |
| `PROXY_PORT` | `8080` | 服务监听端口 |
| `PROXY_API_KEY` | `""` | API Key（为空不校验；支持逗号分隔多个 key） |
| `PROXY_ENABLED_MODELS` | `gemini,kimi` | 启用的模型代理，逗号分隔（如 `gemini,kimi`） |
| `PROXY_KEEP_CHAT` | `false` | 保留会话（不自动删除） |
| `PROXY_BROWSER_HEADLESS` | `false` | 无头模式 |
| `PROXY_BROWSER_POOL_SIZE` | `1` | 每个模型的浏览器实例池大小（并发请求数） |
| `PROXY_USE_LOCAL_CHROME` | `true` | 使用本地 Chrome |
| `PROXY_CHROME_EXECUTABLE` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Chrome 路径 |
| `PROXY_GEMINI_CHAT_URL` | `https://gemini.google.com/app` | Gemini 页面 |
| `PROXY_KIMI_CHAT_URL` | `https://kimi.moonshot.cn` | Kimi 页面 |

### 并发配置（浏览器实例池）

默认每个模型只有 **1** 个浏览器实例，同时只能处理一个请求。如需支持并发，可增大池大小：

```env
# .env
PROXY_BROWSER_POOL_SIZE=2
```

- 每个实例使用**独立的 Chrome profile 目录**（自动复制基础 profile），避免多实例冲突
- 实例 0 直接使用原始 profile，实例 1~N 复制自原始 profile（保留登录态）
- 每个 Chrome 实例约占用 **200-400MB** 内存，请根据机器配置调整
- 请求完成后会自动调用 `new_chat()` 清理页面状态

> ⚠️ **注意**：首次启动多个实例时，若原始 profile 未登录，每个副本可能都需要分别登录。建议先启动单实例完成登录，再增大 pool size。

完整配置参见 `.env.example`。

---

## 扩展新后端

得益于**模板方法模式**和**抽象工厂模式**，添加新后端（如 Claude Web）只需：

1. **创建客户端类**继承 `BaseLLMClient`：

```python
# webui_llm_proxy/clients/claude.py
from webui_llm_proxy.clients.base import BaseLLMClient
from webui_llm_proxy.clients.factory import LLMClientFactory

class ClaudeClient(BaseLLMClient):
    def _get_chat_url(self) -> str:
        return "https://claude.ai/chat"

    def _get_browser_profile(self) -> str:
        return "./browser_data_claude"

    async def _upload_files_impl(self, file_paths: list[str]) -> bool:
        ...

    async def _extract_response_text(self, skip_count: int = 0) -> str:
        ...

# 自动注册到工厂
LLMClientFactory.register("claude", ClaudeClient)
```

2. **无需修改任何现有代码**，工厂会自动识别 `model=claude-xxx` 并创建对应客户端。

---

## 运行测试

```bash
pytest tests/ -v
```

---

## 许可证

MIT

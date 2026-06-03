"""
全局配置模块 — 使用 Pydantic Settings v2（单例 + 类型安全 + 环境变量支持）

所有配置通过环境变量覆盖，支持 .env 文件。
环境变量前缀: PROXY_
"""

from __future__ import annotations

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

# 显式加载 .env，确保嵌套模型也能正确读取环境变量
load_dotenv()


class BrowserSettings(BaseSettings):
    """浏览器配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_BROWSER_")

    headless: bool = Field(default=False, description="是否无头模式运行浏览器")
    slow_mo: int = Field(default=50, description="操作延迟（毫秒）")
    width: int = Field(default=1280, description="浏览器视口宽度")
    height: int = Field(default=800, description="浏览器视口高度")
    user_data_dir: str = Field(default="./browser_data_chrome", description="Gemini Chrome 用户数据目录")
    kimi_user_data_dir: str = Field(default="./browser_data_kimi_v2", description="Kimi Chrome 用户数据目录")
    use_local_chrome: bool = Field(default=True, description="使用本地 Chrome 而非 Playwright Chromium")
    chrome_executable: str = Field(default=r"C:\Program Files\Google\Chrome\Application\chrome.exe", description="Chrome 可执行文件路径")
    chrome_channel: str = Field(default="chrome", description="Chrome 通道")
    pool_size: int = Field(default=1, description="每个模型的浏览器实例池大小（并发请求数）")

    @property
    def viewport(self) -> dict:
        return {"width": self.width, "height": self.height}


class GeminiSettings(BaseSettings):
    """Gemini Web UI 配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_GEMINI_")

    chat_url: str = Field(default="https://gemini.google.com/app", description="Gemini 聊天页面 URL")
    auto_login: bool = Field(default=True, description="首次启动时自动等待用户登录")
    login_wait_timeout: int = Field(default=120, description="登录等待超时（秒）")
    page_load_wait: int = Field(default=3, description="页面加载后等待时间（秒）")
    response_start_timeout: int = Field(default=60, description="等待回复开始的最长时间（秒）")
    stream_idle_timeout: int = Field(default=30, description="流式输出空闲超时（秒）")
    poll_interval_ms: int = Field(default=200, description="DOM 轮询间隔（毫秒）")

    # DOM 选择器
    input_selector: str = Field(default='div[role="textbox"]', description="输入框选择器")
    send_selector: str = Field(default='button.send-button', description="发送按钮选择器")
    response_selector: str = Field(default='.response-content', description="回复内容选择器")
    message_list_selector: str = Field(default='.conversation-container, .chat-history, [role="log"]', description="消息列表选择器")
    user_marker: str = Field(default='[data-sender="user"], .user-message', description="用户消息标识")
    model_marker: str = Field(default='[data-sender="model"], .model-message', description="模型消息标识")
    upload_button_selector: str = Field(default='button[aria-label*="Upload"], button[title*="Upload"], [data-testid="upload-button"]', description="上传按钮选择器")
    file_input_selector: str = Field(default='input[type="file"]', description="文件输入框选择器")
    upload_processing_selector: str = Field(default='.upload-processing, [data-testid="upload-processing"]', description="上传处理指示器选择器")


class KimiSettings(BaseSettings):
    """Kimi Web UI 配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_KIMI_")

    chat_url: str = Field(default="https://kimi.moonshot.cn", description="Kimi 聊天页面 URL")
    auto_login: bool = Field(default=True, description="首次启动时自动等待用户登录")
    login_wait_timeout: int = Field(default=120, description="登录等待超时（秒）")
    page_load_wait: int = Field(default=3, description="页面加载后等待时间（秒）")
    response_start_timeout: int = Field(default=60, description="等待回复开始的最长时间（秒）")
    stream_idle_timeout: int = Field(default=180, description="流式输出空闲超时（秒）")
    max_wait_after_start: int = Field(default=180, description="回复开始后最多等待时间（秒）")
    stable_count_threshold: int = Field(default=45, description="稳定判定阈值（次）")
    poll_interval_ms: int = Field(default=1000, description="DOM 轮询间隔（毫秒）")

    # DOM 选择器
    input_selector: str = Field(default='textarea, div[contenteditable="true"], [data-testid="editor"], .editor, [class*="input"], [class*="textarea"]', description="输入框选择器")
    send_selector: str = Field(default='button[type="submit"], button[aria-label*="发送"], button.send-button', description="发送按钮选择器")
    response_selector: str = Field(default='.chat-message-assistant, .assistant-message, [data-testid="assistant-message"], .message-content', description="回复内容选择器")
    message_list_selector: str = Field(default='.chat-message, .message-item, [data-testid="message-list"]', description="消息列表选择器")
    user_marker: str = Field(default='.chat-message-user, .user-message', description="用户消息标识")
    model_marker: str = Field(default='.chat-message-assistant, .assistant-message', description="模型消息标识")
    upload_button_selector: str = Field(default='button[aria-label*="上传"], button[aria-label*="附件"], button[aria-label*="Upload"], button[aria-label*="Attachment"], [data-testid="attachment-button"]', description="上传按钮选择器")
    file_input_selector: str = Field(default='input[type="file"]', description="文件输入框选择器")
    upload_processing_selector: str = Field(default='.upload-processing, [data-testid="upload-processing"]', description="上传处理指示器选择器")


class UploadSettings(BaseSettings):
    """文件上传配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_UPLOAD_")

    temp_dir: str = Field(default="./data/uploads", description="临时上传文件保存目录")
    max_size_mb: int = Field(default=100, description="单个文件最大大小（MB）")
    max_files: int = Field(default=10, description="单次请求最多上传文件数")
    processing_timeout: int = Field(default=30, description="上传后等待处理完成的最长时间（秒）")

    @property
    def allowed_extensions(self) -> set[str]:
        return {
            '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
            '.pdf', '.txt', '.md', '.csv', '.json', '.docx', '.xlsx',
            '.mp3', '.wav', '.m4a', '.ogg', '.flac',
            '.mp4', '.mov', '.avi', '.mkv', '.webm',
        }

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024


class OpenAISettings(BaseSettings):
    """OpenAI 兼容接口配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_")

    host: str = Field(default="0.0.0.0", description="服务监听地址")
    port: int = Field(default=8080, description="服务监听端口")
    api_key: str = Field(default="", description="API Key，支持逗号分隔多个 key（为空时不校验）")
    model_name: str = Field(default="gemini-pro-via-proxy", description="对外暴露的默认模型名称")
    max_context_length: int = Field(default=8000, description="单次请求最大上下文长度")

    @property
    def api_keys(self) -> set[str]:
        """返回允许的 API Key 集合（自动按逗号分割并去空白）"""
        if not self.api_key:
            return set()
        return {k.strip() for k in self.api_key.split(",") if k.strip()}


class MemorySettings(BaseSettings):
    """记忆管理配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_")

    short_term_rounds: int = Field(default=20, description="短期记忆保留最近轮数")
    long_term_threshold: int = Field(default=10, description="长期记忆压缩阈值")
    memory_file: str = Field(default="./data/memory.json", description="记忆文件路径")
    enable_long_term: bool = Field(default=True, description="是否启用长期记忆")


class LogSettings(BaseSettings):
    """日志配置"""
    model_config = SettingsConfigDict(env_prefix="PROXY_LOG_")

    level: str = Field(default="INFO", description="日志级别")
    format: str = Field(default="%(asctime)s - %(name)s - %(levelname)s - %(message)s", description="日志格式")
    usage_log_dir: str = Field(default="./data/logs", description="使用台账日志目录")


class Settings(BaseSettings):
    """全局配置聚合（单例）"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="PROXY_",
    )

    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    kimi: KimiSettings = Field(default_factory=KimiSettings)
    upload: UploadSettings = Field(default_factory=UploadSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    log: LogSettings = Field(default_factory=LogSettings)
    keep_chat: bool = Field(default=False, description="会话结束后保留对话页面")
    enabled_models: str = Field(default="gemini,kimi", description="启用的模型代理，逗号分隔（如 gemini,kimi）")

    @property
    def enabled_model_list(self) -> list[str]:
        """返回启用的模型前缀列表"""
        return [m.strip().lower() for m in self.enabled_models.split(",") if m.strip()]


# 全局单例实例
settings = Settings()

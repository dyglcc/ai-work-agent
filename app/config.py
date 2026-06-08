from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "env_prefix": "AI_WORK_",
        "extra": "ignore",
    }

    # Anthropic
    ai_provider: str = Field(
        default="anthropic",
        description="AI provider: anthropic or openai",
    )
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API Key",
    )
    anthropic_base_url: str = Field(
        default="",
        description="自定义 API 地址（兼容 Anthropic 协议的第三方服务）",
    )
    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="模型名称",
    )

    # Whisper 语音转写（默认复用同一网关）
    whisper_api_url: str = Field(
        default="",
        description="Whisper API 地址，留空则根据 anthropic_base_url 自动推断",
    )
    whisper_api_key: str = Field(
        default="",
        description="Whisper API Key，留空则复用 anthropic_api_key",
    )
    whisper_model: str = Field(
        default="whisper-1",
        description="Whisper 模型名称",
    )

    # 图片生成（默认复用同一网关）
    image_api_url: str = Field(
        default="",
        description="图片生成 API 地址，留空则根据 anthropic_base_url 自动推断",
    )
    image_api_key: str = Field(
        default="",
        description="图片生成 API Key，留空则复用 anthropic_api_key",
    )
    image_model: str = Field(
        default="dall-e-3",
        description="图片生成模型名称",
    )

    # 钉钉
    dingtalk_enabled: bool = Field(default=False)
    dingtalk_app_key: str = Field(default="")
    dingtalk_app_secret: str = Field(default="")

    # 飞书
    feishu_enabled: bool = Field(default=False)
    feishu_app_id: str = Field(default="")
    feishu_app_secret: str = Field(default="")

    # RAG 知识库
    rag_embedding_url: str = Field(
        default="",
        description="嵌入 API 地址，留空则根据 anthropic_base_url 自动推断",
    )
    rag_embedding_key: str = Field(
        default="",
        description="嵌入 API Key，留空则复用 anthropic_api_key",
    )
    rag_embedding_model: str = Field(
        default="text-embedding-3-small",
        description="嵌入模型名称",
    )
    rag_chunk_size: int = Field(
        default=500,
        description="文档分块大小（字符数）",
    )
    rag_chunk_overlap: int = Field(
        default=50,
        description="分块重叠大小（字符数）",
    )

    # 服务
    openai_api_key: str = Field(
        default="",
        description="OpenAI-compatible API Key, such as DeepSeek",
    )
    openai_base_url: str = Field(
        default="https://api.deepseek.com",
        description="OpenAI-compatible API Base URL",
    )
    openai_model: str = Field(
        default="deepseek-chat",
        description="OpenAI-compatible model name",
    )
    log_level: str = Field(default="INFO")
    file_storage_dir: str = Field(default="")
    public_base_url: str = Field(default="")


settings = Settings()

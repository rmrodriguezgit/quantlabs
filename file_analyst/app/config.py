from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM local OpenAI-compatible. Default points to the QuantLabs llama.cpp service.
    llm_api_url: str = "http://llm:8080/v1/chat/completions"
    model_name: str = "quantlabs-local"
    max_tokens: int = 1600

    # ── Chunking
    chunk_size: int = 6000        # chars por chunk
    chunk_overlap: int = 400      # solapamiento entre chunks
    max_chunks: int = 10          # máx chunks por documento

    # ── Behavior
    default_language: str = "es"
    temperature: float = 0.1
    llm_enabled: bool = True
    llm_timeout_seconds: int = 180
    max_upload_mb: int = 50

    # ── Server
    host: str = "0.0.0.0"
    port: int = 8010
    reload: bool = False

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()

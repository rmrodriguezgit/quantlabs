from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    secret_key: str = "dev-secret"
    jwt_secret: str = "dev-jwt-secret"
    jwt_algorithm: str = "HS256"
    cookie_name: str = "quantlab_token"
    database_url: str = "sqlite:///./quantlab.db"
    redis_url: str = "redis://localhost:6379/0"
    llama_base_url: str = "http://llm:8080"
    quantlab_api_base_url: str = "http://flask:5000"
    allowed_origins: str = "*"
    shell_workdir: str = "/tmp"
    artifact_root: str = "./storage/artifacts"
    session_root: str = "./storage/sessions"
    conversation_root: str = "/app/conversations"
    upload_root: str = "/app/uploads"
    allowed_file_roots: str = "/app/storage,/app/uploads,/app/conversations,/app/notebooks,/tmp"
    allowed_http_hosts: str = "flask,llm,localhost,127.0.0.1,query1.finance.yahoo.com,api.mexc.com"
    allowed_docker_targets: str = "quantlab_harness,quantlab_api,quantlab_llm,quantlab_nginx,quantlab_auth"
    max_upload_mb: int = 100
    model_context_window: int = 16384
    enable_cuda: bool = True
    max_tool_seconds: int = 120
    max_agent_steps: int = 6
    llm_connect_timeout_seconds: int = 10
    llm_read_timeout_seconds: int = 240
    llm_max_tokens: int = 384
    rate_limit_per_minute: int = 60
    mexc_api_key: str = ""
    mexc_api_secret: str = ""
    mexc_live_trading_enabled: bool = False
    gamma_api: str = ""
    data_api: str = ""
    clob_api: str = ""
    polymarket_live_trading_enabled: bool = False
    polymarket_private_key: str = ""
    polymarket_funder_address: str = ""
    polymarket_signature_type: int = 1
    polymarket_chain_id: int = 137



def validate_runtime_secrets(settings: Settings) -> None:
    if not settings.jwt_secret or settings.jwt_secret in {"dev-jwt-secret", "CAMBIA_SECRET"}:
        raise RuntimeError("JWT_SECRET must be configured with a strong secret")


settings = Settings()
validate_runtime_secrets(settings)

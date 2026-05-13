"""Runtime settings loaded from env / .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Server ----
    server_host: str = "0.0.0.0"
    server_port: int = 8080
    environment: str = Field(default="dev", pattern="^(dev|test|uat|prod)$")
    log_level: str = "INFO"

    # ---- Auth ----
    jwt_secret: str = Field(default="change-me-in-prod-please-use-env")
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # ---- Postgres ----
    pg_dsn: str = "postgresql+psycopg://hub:hub@localhost:5432/ticket_hub"
    pg_pool_size: int = 10
    pg_max_overflow: int = 5

    # ---- Redis ----
    redis_url: str = "redis://localhost:6379/0"

    # ---- MinIO / S3 ----
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ticket-hub-attachments"
    s3_region: str = "us-east-1"

    # ---- Feishu ----
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_sso_redirect_uri: str = "http://localhost:8080/api/auth/feishu/callback"
    feishu_app_token: str = ""  # bitable app id (legacy table-as-storage; D6 退役)
    feishu_table_id: str = ""  # ticket bitable table id (legacy)
    feishu_duty_table_id: str = ""  # 值班表 table id（D1 用作 assignment seed）

    # ---- KSM ----
    ksm_base_url: str = "https://ierpuat.kingdee.com"
    ksm_app_id: str = ""
    ksm_app_secret: str = ""
    ksm_tenant_id: str = ""
    ksm_account_id: str = ""
    ksm_user: str = ""

    # ---- Zhichi ----
    zhichi_appid: str = ""
    zhichi_app_key: str = ""

    # ---- LLM Providers (D3 onwards) ----
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    glm_api_key: str = ""

    # ---- PII ----
    pii_master_key: str = ""  # base64-encoded 32-byte AES key; required in prod

    # ---- Webhook auth ----
    webhook_access_token: str = ""

    # ---- Routing ----
    default_pool_user_id: int | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

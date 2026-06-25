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
    glm_model: str = "glm-4.5-flash"  # e.g. glm-4-flash / glm-4-air / glm-4-plus
    dashscope_api_key: str = ""  # 阿里云百炼，OpenAI 兼容模式
    dashscope_model: str = "deepseek-v4-flash"  # e.g. deepseek-v4-pro / deepseek-v3.2
    # 逗号分隔的 failover 顺序；2026-06-11 评测 deepseek-v4-flash 最优故默认在前
    llm_provider_order: str = "dashscope,glm"
    # classify Agent 的 prompt 版本（prompts/classify_{version}.md），评测对比时可切回 v1
    classify_prompt_version: str = "v2"
    # D3-D 拆单判定 Agent；仅写 agent_decisions 审计行，不改工单
    conflict_detect_enabled: bool = True
    conflict_detect_prompt_version: str = "v1"
    # D3-D split 执行器：conf ≥ 阈值自动物化 Child；低于阈值留给主管审批。
    # 默认关闭自动 — 先灰度手动执行，稳定后再开
    split_auto_enabled: bool = False
    split_auto_confidence: float = 0.85
    # D3-E dedup Agent：embedding 召回 + LLM 判定；仅写 agent_decisions 审计行。
    # 向量存 ticket_embeddings JSON 列、Python 余弦召回（当前量级足够；
    # 量大再迁 pgvector）。
    dedup_enabled: bool = True
    dedup_prompt_version: str = "v1"
    dedup_recall_threshold: float = 0.80  # 余弦相似度下限，低于不送 LLM
    dedup_recall_top_k: int = 5  # 送 LLM 判定的候选数上限
    dedup_candidate_pool: int = 200  # 召回扫描的最近工单数
    # D4 优化 v2: 90 天内语义重复自动挂载到目标 hub（AI 判定，主管可 relink 纠偏）。
    # 默认关，先灰度审计；目标须已毕业 hub 且在窗口内才自动挂。
    dedup_auto_mount_enabled: bool = False
    dedup_mount_window_days: int = 90
    dashscope_embedding_model: str = "text-embedding-v4"
    glm_embedding_model: str = "embedding-3"

    # ---- D4 第③段 Vision 多模态 ----
    # 截图识别（报错图 → OCR 文本 + 界面描述），补进 ticket.body 供下游分类/去重。
    # 默认关闭灰度；接的是国内管理大模型（qwen-vl，同 DashScope 边界，无 PII 新增暴露）。
    vision_enabled: bool = False
    vision_model: str = "qwen-vl-max"  # 报错截图要准确 OCR；可换 qwen-vl-plus 省成本
    vision_api_key: str = ""  # 留空则回落 dashscope_api_key
    vision_prompt_version: str = "v1"
    vision_max_images_per_ticket: int = 5  # 单工单最多识别张数（防异常附件刷量）

    # ---- D4 第③段 AI 客服 escalation ----
    # 客户对 AI 客服回答不满意 → cs-escalation webhook → 二次分类（黄金三元组）
    escalation_prompt_version: str = "v1"
    # escalation 自动毕业 hub_issue 的置信门槛（比普通 0.80 高——这条链直接推 Linear）
    escalation_auto_enabled: bool = False
    escalation_auto_confidence: float = 0.85

    # ---- Linear / hub_issue (D4) ----
    linear_api_key: str = ""
    linear_team_id: str = ""  # Linear team ID to create issues in
    # 工单分类后自动创建 hub_issue（conf ≥ 阈值才建）。默认关 — 先灰度主管手动
    hub_issue_auto_enabled: bool = False
    hub_issue_auto_confidence: float = 0.80
    # hub_issue (Bug_fix/Demand) 创建后异步推 Linear。默认关，配好 key 后开
    linear_push_enabled: bool = False
    # D4 优化 v2: 建 Linear 前 hub 级语义去重（命中则 supersede 到已有 hub，不重复推）
    hub_dedup_enabled: bool = True
    hub_dedup_threshold: float = 0.85  # 余弦下限
    hub_dedup_top_k: int = 5
    hub_dedup_prompt_version: str = "v1"

    # ---- PII ----
    pii_master_key: str = ""  # base64-encoded 32-byte AES key; required in prod

    # ---- Webhook auth ----
    webhook_access_token: str = ""

    # ---- Routing ----
    default_pool_user_id: int | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

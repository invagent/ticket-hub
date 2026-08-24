"""Runtime settings loaded from env / .env."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
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

    # ---- 存储（MinIO）----
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ticket-hub-attachments"
    minio_public_base: str = ""  # 公开访问前缀(nginx反代)，空则用 endpoint 拼
    minio_secure: bool = False

    # ---- 附件流水线灰度 ----
    attachment_pipeline_enabled: bool = False
    attachment_pipeline_dry_run: bool = True
    attachment_max_bytes: int = 10 * 1024 * 1024
    attachment_max_attempts: int = 3

    # ---- Feishu ----
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_sso_redirect_uri: str = "http://localhost:8080/api/auth/feishu/callback"
    feishu_app_token: str = ""  # bitable app id (legacy table-as-storage; D6 退役)
    feishu_table_id: str = ""  # ticket bitable table id (legacy)
    feishu_duty_table_id: str = ""  # 值班表 table id（D1 用作 assignment seed）
    # 知识库 wiki（ADR-0016 P3 反思闭环 KB/FAQ 调试地基；需 wiki:wiki + docx readonly）。
    # space_id 定位知识空间；root_node 可选（只读某目录子树，留空=整个空间）。
    # 从 wiki 页面链接 /wiki/<node_token> 反查 space_id（scripts/feishu_wiki_dump.py）。
    feishu_wiki_space_id: str = ""
    feishu_wiki_root_node: str = ""
    # 生成「在飞书打开」链接的租户域名前缀（无 trailing slash）；空则不出链接
    feishu_wiki_link_base: str = "https://icn1dae2f6c3.feishu.cn"

    # ---- KSM ----
    ksm_base_url: str = "https://ierpuat.kingdee.com"
    ksm_app_id: str = ""
    ksm_app_secret: str = ""
    ksm_tenant_id: str = ""
    ksm_account_id: str = ""
    ksm_user: str = ""
    # KSM 回写操作员身份（lock/handle/supply 都要带 account/accountName/accountNumber）
    ksm_handler_name: str = ""  # 处理人姓名 → account + accountName
    ksm_handler_number: str = ""  # 处理人工号 → accountNumber（飞书员工搜索接口获取）

    # ---- D4 第②段: KSM 出站回写 sender（消费 sync_outbox） ----
    # 默认全关 + dry_run：建好 + 部署后，先 dry_run 观察组装的 payload，
    # 再翻 ksm_writeback_dry_run=false 真打 KSM。与 Phase 0 灰度同剧本。
    ksm_writeback_enabled: bool = False  # 总开关：关时 drain 直接跳过
    ksm_writeback_dry_run: bool = True  # 开 enabled 但 dry_run → 只组装+标 skipped，不真发
    ksm_writeback_batch: int = 20  # 每轮 drain 处理的 pending 行数上限
    ksm_writeback_max_attempts: int = 5  # 失败重试上限，超过标 failed 转人工
    # 入库即接管受理：KSM 工单入库拉详情后主动 lock(+handle) 抢占锁定。默认关。
    # 复用 ksm_writeback_dry_run 作试运行（开 enabled + dry_run → 只组装打日志不真发）。
    # ⚠️ 生产 KSM 地址下一开就真接管每条新工单，务必先 dry_run 验证字段再放开。
    ksm_auto_takeover_enabled: bool = False

    # ---- Zhichi ----
    zhichi_appid: str = ""
    zhichi_app_key: str = ""

    # ---- 智齿出站回写 sender（消费 sync_outbox，镜像 KSM 灰度剧本）----
    # 默认全关 + dry_run：配好 appid/app_key + 部署后先 dry_run 观察组装的 payload，
    # 再翻 zhichi_writeback_dry_run=false 真打智齿。与 KSM 同剧本。
    zhichi_base_url: str = "https://www.soboten.com"
    zhichi_writeback_enabled: bool = False  # 总开关：关时 drain 直接跳过
    zhichi_writeback_dry_run: bool = True  # 只组装+标 skipped，不真发
    zhichi_writeback_batch: int = 20  # 每轮 drain 处理的 pending 行数上限
    zhichi_writeback_max_attempts: int = 5  # 失败重试上限，超过标 failed 转人工
    zhichi_fallback_agent_name: str = "莉莉"  # deal_agent_name 为空时的默认回复坐席

    # ---- Operation 自动答复（调 ai_cs replay 生成答复回写客户）----
    # 默认关：开了才对新毕业的 Operation hub_issue 自动答复。出站真发仍受
    # ksm/zhichi_writeback_enabled + dry_run 二层灰度保护（双保险）。
    operation_auto_reply_enabled: bool = False
    operation_auto_reply_min_length: int = 10  # 答复短于此视为无效，留主管
    operation_auto_reply_batch: int = 10  # 异步 drain 每轮扫描/处理的 hub 数上限

    # ---- Operation T+N 自动关闭（answered 停留超 N 天未被驳回 → 自动 closed）----
    operation_auto_close_enabled: bool = False
    operation_auto_close_days: int = 7  # 自然日；驳回会转回 processing 并刷新 op_status_changed_at

    # ---- Operation 答复准确率闸门 ----
    # 答复准确率闸门：off=不打分同现状 / observe=打分记审计但照常直发（采集分布）
    # / enforce=低置信存草稿转主管审核 / review=全部存草稿转主管审核（无自动直发）
    # review 模式仍打分供主管参考，但无论分数高低都存草稿转 reviewing 队列，
    # 由主管人工确认后经 POST /reply 发送——用于「所有答复必须人工确认」的场景。
    operation_answer_accuracy_mode: Literal["off", "observe", "enforce", "review"] = "off"
    operation_answer_accuracy_threshold: int = 90  # 0-100，仅 enforce 生效

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
    # 提示词版本统一走 skill_prompts 三槽（draft/current/previous，ADR-0016 P1），
    # 不再有 *_prompt_version 配置项。
    # ADR-0016 P2c：拆单判定并入 triage（conflict_detect 已退役删除）。
    # D3-D split 执行器：conf ≥ 阈值自动物化 Child；低于阈值留给主管审批。
    # 默认关闭自动 — 先灰度手动执行，稳定后再开
    split_auto_enabled: bool = False
    split_auto_confidence: float = 0.85
    # ADR-0016 P2e：ticket 级 dedup agent 退役（hub_dedup 唯一主查重），
    # dedup_* 召回/自动挂载配置项一并移除；embedding 配置保留给 hub_dedup。
    dashscope_embedding_model: str = "text-embedding-v4"
    glm_embedding_model: str = "embedding-3"

    # ---- D4 第③段 Vision 多模态 ----
    # 截图识别（报错图 → OCR 文本 + 界面描述），补进 ticket.body 供下游分类/去重。
    # 默认关闭灰度；接的是国内管理大模型（qwen-vl，同 DashScope 边界，无 PII 新增暴露）。
    vision_enabled: bool = False
    vision_model: str = "qwen-vl-max"  # 报错截图要准确 OCR；可换 qwen-vl-plus 省成本
    vision_api_key: str = ""  # 留空则回落 dashscope_api_key
    vision_max_images_per_ticket: int = 5  # 单工单最多识别张数（防异常附件刷量）

    # ---- D4 第③段 AI 客服 escalation ----
    # 客户对 AI 客服回答不满意 → cs-escalation webhook → 二次分类（黄金三元组）
    # escalation 自动毕业 hub_issue 的置信门槛（比普通 0.80 高——这条链直接推 Linear）
    escalation_auto_enabled: bool = False
    escalation_auto_confidence: float = 0.85

    # ---- Phase 1 知识反哺闭环：AI 客服 open-api（skill-management.json）----
    # 主管从 escalation 工单反思 → 改 AI 客服 skill draft → replay 试跑对比 → 发布。
    # 默认关；配好 base_url + appid/app_key 后开。见 adapters/ai_cs/。
    knowledge_feedback_enabled: bool = False
    ai_cs_base_url: str = "http://localhost:9090"
    ai_cs_app_id: str = ""  # AI 客服 open-api appid（沿用 sample AGENT_APPID 语义）
    ai_cs_app_key: str = ""  # 签名密钥 app_key（MD5(appid+create_time+app_key)）
    ai_cs_managed_skills: str = "customer-service,customer-service-feishu"
    # replay 走 LLM 生成，AI 客服服务端可能较慢；客户端超时（秒）。可 .env 覆盖。
    ai_cs_timeout_seconds: float = 180.0
    # 反思诊断工作台：LLM 反思推断（三步排查 → 病因判定），主管手动触发

    # ---- AI 产品模块归类 ----
    # 入库链 module_resolve：AI 判产品线/模块。默认关，先灰度。
    module_classify_enabled: bool = False
    # AI 置信度 ≥ 此值才采用 AI 结果；否则回退（按源系统分类找 → 相似 → 兜底）。
    module_classify_confidence: float = 0.6
    # 兜底产品线/模块（现有 active 目录里的「其他非发票云问题」）。
    module_fallback_product_line_code: str = "PROLINE6067"
    module_fallback_module: str = "其他非发票云问题"

    # ---- Linear / hub_issue (D4) ----
    linear_api_key: str = ""
    linear_team_id: str = ""  # Linear team ID to create issues in
    # 转研发推送出口：默认走飞书 webhook（下面三项），而非直连 Linear GraphQL。
    # linear_webhook_enabled=True 时 push_hub_issue_to_linear 走 webhook；
    # False 回落直连 Linear（linear_api_key + linear_team_id + linear_push_enabled）。
    linear_webhook_enabled: bool = True
    linear_webhook_url: str = (
        "http://123.57.100.193/linear-webhook/feishu-ticket"
        "?access_token=cf23a80b86949372c2cddab05760a04309b4b6ec8e1ecc0a1fb58a167925bc3a"
    )
    linear_webhook_token: str = ""  # 备用：如需与 url 分离传 token（当前 token 内嵌于 url）
    linear_webhook_timeout_seconds: float = 30.0
    # feishuUrl / 工单详情链接前缀（拼 {base}/tickets/{ticket_id}）。空则该字段留空。
    hub_public_base_url: str = ""
    # productLine 顶级产品默认值（webhook payload 的 productLine 字段，可空时回落此值）
    linear_webhook_default_product_line: str = "金蝶发票云"
    # 工单分类后自动创建 hub_issue（conf ≥ 阈值才建）。默认关 — 先灰度主管手动
    hub_issue_auto_enabled: bool = False
    hub_issue_auto_confidence: float = 0.80
    # hub_issue (Bug_fix/Demand) 创建后异步推 Linear。默认关，配好 key 后开
    linear_push_enabled: bool = False
    # 研发类(Bug_fix/Demand)自动毕业后，推 Linear 前是否需主管确认分类。
    # 默认开：agent 自动毕业的研发类进 pending_review 待确认队列，不自动推 Linear。
    # 主管手动毕业(create-hub-issue)不受此闸门影响，视为已确认直推。
    require_review_before_linear: bool = True
    # 闸门①：全类型毕业后停 pending_review 待确认分类（默认 None → 回落 require_review_before_linear）
    gate_classify_enabled: bool | None = None
    # 闸门③：研发类推 Linear 前停 pending_linear_review 待处理人确认（默认开）
    gate_linear_push_enabled: bool = True
    # D4 优化 v2: 建 Linear 前 hub 级语义去重（命中则 supersede 到已有 hub，不重复推）
    hub_dedup_enabled: bool = True
    hub_dedup_threshold: float = 0.85  # 余弦下限
    hub_dedup_top_k: int = 5

    # ---- PII ----
    pii_master_key: str = ""  # base64-encoded 32-byte AES key; required in prod

    # ---- Webhook auth ----
    webhook_access_token: str = ""

    # ---- Routing ----
    default_pool_user_id: int | None = None

    # ---- D4 优化 v2: SLA 工作日感知 ----
    # 开后 SLAWatcher 超时判定改用「工作日小时」（扣除周末+holidays 节假日）。
    # 默认关，保持墙钟行为不变；填好 holidays 日历后再开。
    sla_workday_aware: bool = False

    # ---- SLA 监控调度 ----
    # SLAWatcher(超时检测→notification_log) + EscalationWorker(2h 升级链) 的 beat 调度开关。
    # 默认关：SLAWatcher/EscalationWorker 代码早已就绪但从未挂 beat；开启前需知悉——
    # 首次开启会对当前所有积压超时工单一次性生成 sla_overdue 通知，请在低峰期开或先清积压。
    sla_watcher_enabled: bool = False
    # 无 assignee 的超时实体，通知回落到该用户（一般是值班主管）；None 则跳过并计 skipped。
    # 未单独配置时回落到 default_pool_user_id。
    sla_fallback_recipient_id: int | None = None

    @model_validator(mode="after")
    def _resolve_gate_classify(self) -> "Settings":
        if self.gate_classify_enabled is None:
            object.__setattr__(self, "gate_classify_enabled", self.require_review_before_linear)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

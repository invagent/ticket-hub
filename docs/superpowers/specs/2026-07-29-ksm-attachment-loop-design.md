# KSM 附件闭环设计（2026-07-29）

## 背景

`project_gaps_audit` P1 缺口 #6：KSM 附件 → 存储 → vision OCR 闭环未通。`storage_key` 字段无一处赋值，KSM ingester 不建 attachment 行，存储层（MinIO/S3）零代码（config 只有占位）。

参考旧项目 `/Users/junill/Documents/04_claude/01_ticket/ticket-hub/`（feishu-python 架构）也是「文档化但 stub」——它有 MinIO client + Attachment 模型 + webhook 流程，但下载转存那步是 TODO（`intake/service.py:37`）。KSM 附件下载 API 极简：`GET {url}` 带 `User-Agent: Mozilla/5.0`，无鉴权，返回 bytes（`KSM接口速查.md:244-248`）。

## 现状（勘察结论）

本项目比参考项目建得更多，已就绪的：
- `VisionClient.extract()`（`app/core/llm_router/vision.py:87`）支持 **url 和 bytes 两条路径**（bytes 路径 base64 成 data URL）
- `attachments` 表 / 模型（`app/models.py:977`，迁移 0012），列含 `source_url` / `storage_key`(String512,nullable) / `filename` / `mime` / `size_bytes` / `kind` / `vision_status` / `extracted_text` / `vision_model` / `vision_cost_usd`
- `KSMClient.download_attachment(url: str) -> bytes`（`adapters/ksm/client.py:264`，带浏览器 UA，**零生产调用者**）
- ingest 链 vision_extract 在 classify 前跑（`app/api/webhooks.py:103`）

三个缺口：
1. **KSM ingester 不建 attachment 行**（`ksm_ingester.py`）；`ksm_payload.py` 不解析附件字段
2. **无存储层**：`config.py:36-41` 的 `s3_*` 是占位、零代码；`storage_key` 从未写入
3. **vision_extract 的 storage_key 分支是 stub**（`vision_extract.py:104-109` 只 source_url 直传，跳过只有 storage_key 的行）

## 决策（用户拍板）

- **范围**：仅 KSM（智齿 file_str / escalation 存储降级留后面）
- **附件字段**：按参考旧项目 —— KSM raw 的 `attachment` 数组，每项取 `url` 字段。⚠️ 无真实 payload 样本佐证当前 KSM 版本字段名一致，上线前需真 payload 验证；解析写得容错（字段缺失优雅降级不报错）
- **存储后端**：MinIO SDK（参考项目同款，非 boto3）；生产已有 MinIO（`fpy-jfsv.kingdee.com`）
- **执行时机**：入站同步**建行**（source_url，storage_key=NULL），下载+上传+OCR **异步**（Celery beat 扫 queued）
- **已知取舍**：OCR 异步 → KSM classify 跑在纯文本 body 上（无 OCR 文本）。OCR 文本落库后惠及 dedup / 展示 / 人工复核，但不参与初次分类。**OCR 后重分类不在本期范围**。

## 架构

```
KSM subscribeCallback (含 attachment 数组)
  → ksm_payload.from_subscribe_callback  【改】解析 raw['attachment'][].url → attachment_urls
  → ksm_ingester.ingest                   【改】同步建 Attachment 行(source_url, storage_key=NULL, vision_status='queued')
  → ingest 链照常 classify（无 OCR 文本）

Celery beat（每 N 分钟，独立开关 + dry_run）  【新】异步附件流水线
  → drain_pending_attachments():
      扫 Attachment where vision_status=='queued' AND source_url NOT NULL AND storage_key IS NULL AND kind=='image'
      逐条 process_one：
        download_attachment(source_url) → MinIO put_bytes → 回写 storage_key/size/mime
          → VisionClient.extract(image_bytes) → extracted_text + 追加 ticket.body[附件识别]段
          → vision_status='extracted'（失败重试/超限 'failed'，逐条隔离不阻塞）
```

### 组件边界（4 个）

1. **payload 解析**（改 `app/services/ingest/ksm_payload.py`）：纯函数从 KSM raw dict 提取 `attachment_urls: list[str]`。容错：`attachment` 缺失/非数组/项无 url → 返回空列表，不抛。
2. **建行**（改 `app/services/ingest/ksm_ingester.py`）：入站同步为每个 url 建 Attachment 行（`source_url=url`, `storage_key=None`, `kind='image'`, `vision_status='queued'`）。不下载。
3. **存储服务**（新 `app/core/storage/minio_store.py`）：`MinioStore.put_bytes(key, data, content_type) -> str`（返回 public_url）+ `ensure_bucket()` + `public_url(key)`。惰性建 MinIO client（同步 SDK）。
4. **异步流水线**（新 `app/services/attachments/pipeline.py` + beat task + 主管端点）：串 download（复用 `KSMClient.download_attachment`）→ 存储 → OCR（复用 `VisionClient.extract` bytes 路径）。

### 隔离方案（新状态值 queued）

ingest 链的同步 `vision_extract` 扫 `vision_status=='pending'`（escalation 源，有公开 source_url，DashScope 自抓）。KSM 附件的 source_url 是内网 URL，DashScope 抓不到 → 若也用 `pending` 会被 vision_extract 误抓失败。

方案：**新增状态值 `queued`**。
- KSM 建行用 `vision_status='queued'`
- ingest 链 `vision_extract` 只扫 `pending`（不动，天然跳过 queued）
- 异步流水线只扫 `queued`
- 需迁移 `0022` 扩 `ck_attachments_vision_status` CHECK 从 `('pending','extracted','skipped','failed')` 加 `queued`

## 配置（`app/config.py`）

`s3_*` 占位（`config.py:36-41`）改为 MinIO 语义命名：
```python
minio_endpoint: str = "localhost:9000"        # 生产 fpy-jfsv.kingdee.com:xxxx
minio_access_key: str = ""
minio_secret_key: str = ""
minio_bucket: str = "ticket-hub-attachments"
minio_public_base: str = ""                   # 公开访问前缀(nginx反代)，空则用 endpoint 拼
minio_secure: bool = False                    # https 与否

attachment_pipeline_enabled: bool = False     # 灰度开关，默认关
attachment_pipeline_dry_run: bool = True      # 默认只建行不下载
attachment_max_bytes: int = 10 * 1024 * 1024  # 单附件上限 10MB，超限标 skipped
attachment_max_attempts: int = 3              # 下载/上传失败重试上限
```

## 存储服务细节

- MinIO SDK **同步**；流水线跑在 Celery worker（同步上下文），直接调用，无需 asyncio.to_thread（与参考项目 async 服务层不同）
- 未配 endpoint/key 时 `MinioStore` 构造抛清晰错误；流水线在 enabled 但配置缺失时标 failed 转人工，**绝不静默成功**
- key 生成：`ksm/{ticket_id}/{att_id}_{safe_filename}`，确定性 + 幂等（重跑覆盖同 key）
- 依赖：加 `minio` SDK 到 requirements

## 流水线细节（`process_one` 确定性步骤）

1. **dry_run 短路**：`attachment_pipeline_dry_run` 开 → 标 skipped（原因 dry_run），不下载
2. **下载**：`download_attachment(source_url)` → bytes；超 `attachment_max_bytes` → skipped（原因 oversize）
3. **上传**：`put_bytes(key, bytes, content_type)` → 回写 `storage_key` + `size_bytes` + `mime`
4. **OCR**：`VisionClient.extract(image_bytes=bytes, mime=...)` → 回写 `extracted_text`/`vision_model`/`vision_cost_usd` + 追加 ticket.body（`[附件识别]` 段，与现有 vision_extract 格式一致）
5. **状态**：全成功 → `extracted`；任一步异常 → attempts++，未超上限留 queued 下轮重试，超 `attachment_max_attempts` → `failed` + error 落审计。逐条 try/except 隔离

幂等：只处理 `storage_key IS NULL` 且 `vision_status=='queued'` 的行；终态不重扫。

**重试计数字段**：已确认 attachments 表（`models.py:977-1018`）无 attempts/error 列。迁移 0022 新增 `download_attempts: Integer NOT NULL server_default '0'` + `last_error: String(512) nullable`。同时更新模型的 CheckConstraint（`models.py:993-996`）加 `queued`。

## 触发

- Celery beat `drain_attachments_every_Nmin`（`app/celery_app.py`）——key/enabled 未配自动跳过
- 主管手动 `POST /api/supervisor/drain-attachments`（require_supervisor，同步看成败，镜像 `drain-ksm-writeback`）

## 保留不动

- 现有 `vision_extract.py`（ingest 链同步、source_url 直传）**不改**——服务 escalation 源，扫 `pending`
- escalation_ingester 建行逻辑不改（仍 `pending` + source_url）

## 测试策略

- **payload 解析**：unit——正常 attachment 数组、缺失字段、非数组、项无 url、空 → 各自返回
- **建行**：unit——KSM ingest 后 Attachment 行数/字段正确（source_url 落、storage_key NULL、vision_status='queued'）；无附件时不建行
- **存储服务**：unit（mock MinIO SDK）——put_bytes 返回 public_url、key 生成规则、未配置抛错；集成（testcontainers MinIO，`@pytest.mark.integration`）真上传
- **流水线**：unit（mock download + store + vision）——正常 extracted、dry_run skipped、oversize skipped、下载失败重试、超上限 failed、逐条隔离、幂等（queued-only）、OCR 文本追加 body
- **端点**：unit——drain-attachments require_supervisor（403 非主管）、返回 scanned/stored/extracted/skipped/failed
- **迁移 0022**：确认 CHECK 约束升级 + 新列，up/down 可逆
- 全量：后端 `make lint` + `make unit`（≥70%）；改了端点 → `make gen-types` + `make check-types`

## 部署注意

- **有迁移 0022**（CHECK 约束 + 新列）——SIT 部署需 `docker compose -f deploy/docker-compose.sit.yml run --rm backend alembic upgrade head`
- 加了 `minio` 依赖 → 镜像 rebuild（SIT git 驱动，`up -d --build`）
- 改了端点 → `make gen-types` 提交
- 灰度上线剧本（镜像 KSM writeback）：配 MinIO endpoint/key + `attachment_pipeline_enabled=true` + 先 `dry_run=true` 观察建行 → 再 `dry_run=false` 真下载
- ⚠️ 上线前用真实 KSM subscribeCallback payload 验证附件字段名（本设计按参考旧项目 `attachment[].url`，当前 KSM 版本未确证）

## 范围与非目标

- ❌ 智齿附件（file_str）——留后面
- ❌ escalation 附件转存 MinIO（现只 source_url 直传，够用）——留后面
- ❌ OCR 后重分类——本期不做
- ❌ 出站方向（附件回传 KSM 的 files base64）——非本任务
- ❌ 非 image 附件（pdf/video）的 OCR——kind 只处理 image，其余建行但流水线跳过

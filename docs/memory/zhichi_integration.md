---
name: zhichi-integration
description: 智齿双向打通实施进展与关键发现（2026-07-14）
metadata: 
  node_type: memory
  type: project
  originSessionId: c37ff699-b29d-4e5e-a515-a05c4c6b880f
---

# 智齿双向打通实施（feat/zhichi-integration 分支）

## 已完成 6/8 任务

- Task 1 ✅ config 加 zhichi_base_url/writeback_enabled/dry_run/batch/max_attempts/fallback_agent_name + .env.sit.example
- Task 2/3 ✅ **原本就已存在**：`adapters/zhichi/` 完整实现（client.py 9.5KB + types + exceptions），9 测试全绿。API：`ZhichiConfig(appid=,app_key=,base_url=)`、`ReplyTicketRequest(ticket_id=, ...)`、`ZhichiClient.reply_ticket(req)` / `get_agent_by_name(name)->Agent|None` / `list_agents` / `upload_file`
- Task 4 ✅ 入站信封解析 `_flatten_envelope()` — fields 中文块主源 + raw 兜底 + extend_fields_list (field_type=6 取 field_text)。source_payload 存整个信封。向后兼容旧扁平格式。11 测试全过
- Task 5 ✅ `services/zhichi/writeback.py` — ZhichiWritebackSender，kind→ticket_status 映射（reply/release_note/status released→3，supply/progress_note→2，status in_progress→skip），坐席兜底（deal_agent_name 空→莉莉，查不到 failed），dry_run/enabled 阀。10 测试全过
- Task 6 ✅ writeback_task.py 定时 drain + beat 每2min + `POST /api/supervisor/drain-zhichi-writeback` + openapi.json + types.ts 同步。2 端点测试全过

## Task 7 全量单测发现的问题

**智齿相关全绿**。1 个不相关失败：`tests/unit/adapters/test_glm_client.py::test_network_error` 报 GLM HTTP 502——非本次改动引入（可能是网络波动或既有测试环境问题）。

**Why**: 单测 826 passed / 1 failed，唯一失败是 GLM adapter 网络测试，与智齿无关。

**How to apply**: 继续 Task 8 部署 SIT 前先确认 GLM 测试是否为既有 flaky（跑几次看是否稳定失败）；如稳定失败则可能有既有回归。

## 关键决策（都已锁定）
- base url: `https://www.soboten.com`（用户环境国内域名，虽然智齿原生文档写 sobot.com）
- 兜底坐席: 「莉莉」（存在），查不到 failed 转人工不静默跳过
- 结构化拼接不做（YAGNI）
- 灰度镜像 KSM：enabled=false + dry_run=true 默认

## Task 7 ✅（2026-07-14）
lint 全绿：ruff check + format + mypy（115 files 0 issues）。全量单测 826 passed / 1 failed（GLM `test_network_error` 在 main 分支也失败——既有 flaky 真实网络测试，与本次改动无关，已确认）。

## Task 8 ✅ SIT 部署完成（2026-07-14）
- 合并 feat/zhichi-integration → main → push GitHub（`2ef109e`）
- SIT git pull + `docker compose -f deploy/docker-compose.sit.yml up -d --build` 重建镜像成功
- 健康端点 :9095/health = 200
- **入站信封解析验证**：造真实智齿 `{source, raw, fields}` webhook 请求，落库字段全对：产品线=发票云、模块=发票管理、联系人张三/手机/邮箱、`source_payload.raw.deal_agent_name=莉莉`、`raw.ticket_level=2`
- **主管 drain 端点验证**：`POST /api/supervisor/drain-zhichi-writeback` 通，响应 `{enabled: false, dry_run: true, scanned: 0}`（默认灰度）

## 分支/PR
`feat/zhichi-integration` → main 已合并推 GitHub。共 7 次 commit：spec、plan、Task1、Task4、Task5、Task6、Task7 format 修复。

## 真打验证待用户在 SIT 手动完成
1. 在 `/data/hub-issue/deploy/.env` 配 `ZHICHI_APPID`/`ZHICHI_APP_KEY`（旧 feishu-python 项目的 .env 里可能有）+ `ZHICHI_WRITEBACK_ENABLED=true`（保持 `ZHICHI_WRITEBACK_DRY_RUN=true`）
2. 重启后主管对智齿 hub_issue 回复 → 触发 drain 端点看 log 里组装的 payload
3. 稳了翻 `ZHICHI_WRITEBACK_DRY_RUN=false` → 真发智齿

相关 [[reference-resources]]、[[project-deployment]]

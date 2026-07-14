---
name: reference-resources
description: 智齿/KSM 对接的参考项目与接口速查文档位置
metadata: 
  node_type: memory
  type: reference
  originSessionId: c37ff699-b29d-4e5e-a515-a05c4c6b880f
---

打通 KSM/智齿 交互时的权威参考资料（都在 `/Users/junill/Documents/04_claude/01_ticket/` 下）：

- **旧版参考项目** `ticket-hub/`（feishu-python 架构，2026-06-23）：有完整的 `app/integrations/zhichi_client.py` + `ksm_client.py` + `app/pipeline/writeback.py`，是智齿出站移植的直接蓝本。
- **KSM接口速查.md**：三步鉴权（getAppToken→login→access_token query）、lock/handle/supply/return/split 全部字段。KSM 无独立关单接口，关单靠 handleKsmOrder(isDeal="2") 推进流程。生产 base `https://ierp.kingdee.com`。
- **智齿接口速查.md**：⚠️旧系统包装版，base 写的是 soboten.com。**以原生文档为准**。
- **智齿原生接口**（https://developer.zhichi.com/pages/950d89/，权威）：base `https://www.sobot.com`；`GET /api/get_token?appid=&create_time=&sign=` 签名 `md5(appid+create_time+app_key)`（create_time秒级），返回 item.token/expires_in(86400s/24h)，header 带 token；核心回复 `POST /api/ws/5/ticket/save_ticket_reply`（必填 ticketid/ticket_title/ticket_content/get_ticket_datetime/reply_type/reply_agentid/reply_agent_name/ticket_status/ticket_level，选填 reply_content/reply_file_str）；ticket_status 枚举 0未受理/1受理中/2等待回复/3已解决/99已关闭；查坐席 `GET /api/ws/5/ticket/get_data_dict`→item.agent_list[](agentid+agent_name，需缓存)；附件 `POST /api/ws/5/ticket/upload_file`(multipart)；**成功判断 `ret_code=="000000"`**。
- **工单参数.txt / agent答复示例.txt / kSM历史数据.xlsx**：样例数据。

新项目 hub-issue 现状：KSM 出站已实现（`services/ksm/writeback.py` + `adapters/ksm/`）但从未真打验证（enabled=false+dry_run=true）；智齿出站完全缺失（无 adapters/zhichi，ZHICHI_APPID/APP_KEY 是空占位，cascade 生成的 zhichi outbox 行永久 pending）。相关 [[project-deployment]]。

## 智齿打通交互 · 已定决策（2026-07-13）
- **范围**：入站字段映射修复 + 出站回写新建，一起做。
- **入站真实格式**：智齿 webhook body = `工单参数.txt` 的智齿段，三层信封 `{source, raw, fields}`。解析优先 `fields` 中文块（产品线/联系人/客户现成），`raw` 兜底（deal_agent_name/ticket_level/file_str/extend_fields_list 只在 raw）；source_payload 存整个信封。
- **出站 base url**：`https://www.soboten.com`（用户环境国内域名，非原生 sobot.com）。
- **出站映射**：kind reply/status released/release_note → ticket_status=3 关单；supply/progress_note → ticket_status=2 不关单；status in_progress → skip（智齿无接管概念）。
- **坐席身份**：回复用工单 `deal_agent_name` → get_data_dict 查 agentid；deal_agent_name 为空 → 用默认坐席「**莉莉**」（存在）；连莉莉都查不到 → failed 转人工报错（不静默跳过）。
- **灰度**：镜像 KSM，zhichi_writeback_enabled(默认false)/zhichi_writeback_dry_run(默认true)。

"""Source-system HTTP adapters.

Each adapter is a thin client around an external API:
  - ksm:    KSM 工单系统（金蝶, primary writer in v1）
  - feishu: 飞书 OpenAPI（Bitable, attachments, IM, contact directory）
  - zhichi: 智齿工单系统
  - zammad: D2 引入
  - linear: D4 引入

Design principles:
  - Class-based clients (not module globals) — testable, multi-tenant capable
  - httpx (sync) — drop-in for requests with cleaner timeout / retry semantics
  - Token cache is per-instance (via TokenCache); injectable for tests
  - Behavior parity with feishu-python preserved during D1 (decision R3)
"""

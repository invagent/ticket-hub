"""Cascade services (D4 第②段): hub_issue → source tickets fan-out.

reply_sync   — Operation 回复版本化 + 级联 tickets 缓存 + outbox 入队（决策 15）
status_cascade — hub 状态变更级联源工单 + outbox 入队（决策 14）
"""

-- 清理 SIT mock 业务流水数据（保留 users/skill_prompts/分工/system_settings/sources/产品线目录）
-- 执行前必须已 pg_dump 备份。单事务，先子后父。
-- 用法: psql ... -f cleanup_sit_mock.sql
BEGIN;

-- 先解开 tickets ↔ hub_issues 自引用，避免 FK 阻塞
UPDATE tickets SET hub_issue_id = NULL;

-- 派生/历史表
DELETE FROM agent_decisions;
DELETE FROM status_history;
DELETE FROM ticket_hub_issue_history;
DELETE FROM hub_issue_reply_history;
DELETE FROM hub_issue_linear_issues;
DELETE FROM hub_issue_relations;
DELETE FROM sync_outbox;
DELETE FROM attachments;
DELETE FROM ticket_embeddings;
DELETE FROM notification_log;
DELETE FROM customer_merge_history;
DELETE FROM materialized_metrics;

-- 主业务表
DELETE FROM tickets;
DELETE FROM hub_issues;
DELETE FROM customer_identities;
DELETE FROM customers;

COMMIT;

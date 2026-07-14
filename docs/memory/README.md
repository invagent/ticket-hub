# 项目记忆快照（Agent Memory Snapshot）

本目录是 Claude Code 会话的**跨会话记忆快照**——Claude 私有 `~/.claude/projects/<slug>/memory/` 目录的拷贝，人工同步过来进 git，让团队其他成员/未来会话（其他机器）能拿到同样的项目上下文。

## 目录清单

- **MEMORY.md** — 索引（其他 md 的一行摘要）
- **user_profile.md** — 项目负责人角色/工作方式
- **project_deployment.md** — UAT/SIT 部署细节（服务器/端口/DB/Redis/nginx，**口令一律占位，见 deploy/.env**）
- **project_fixes.md** — 已修复的兼容性问题根因（FastAPI/Starlette/seccomp/PG 加密等）
- **reference_resources.md** — 智齿/KSM 对接的参考项目位置 + 接口速查文档位置 + 已锁定决策
- **zhichi_integration.md** — 智齿双向打通实施进展（feat/zhichi-integration 分支）

## 敏感信息约定

**绝不**在这些文档里写：口令 / API key / 私钥 / access token。这些值一律用占位符 `<PLACEHOLDER>` 或指向 `deploy/.env`（gitignored）。IP/端口/主机名/数据库名可以写，那是部署文档级别的基础信息。

## 同步方式

Claude 会话在自己私有记忆里持续写入项目上下文；需要把新增/更新的记忆分享出来时，人工从 `~/.claude/projects/-Users-junill-Documents-04-claude-01-ticket-hub-issue/memory/` 复制过来 + 脱敏 + commit。反向恢复：把这里的文件 `cp` 回私有 memory 目录即可。

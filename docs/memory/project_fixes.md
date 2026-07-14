---
name: project-fixes
description: 已修复的兼容性问题和根因
metadata: 
  node_type: memory
  type: project
  originSessionId: e1d618c3-a67f-419c-9a88-588833782e7f
---

## FastAPI/Starlette 兼容性修复（2026-06-28）

**问题**：pip 装了最新 FastAPI 0.138.1 + Starlette 1.x，导致两个问题：
1. 同步路由 + `Depends(get_session)` 在 async middleware 里报 `can't start new thread`
2. `status_code=204` 路由报 `AssertionError: Status code 204 must not have a response body`

**根因**：Rocky Linux 9 的 Docker seccomp profile 限制了 `clone()` 系统调用，导致容器内完全无法创建线程。FastAPI 在处理同步依赖时需要线程池，于是报错。

**修复**：
- `pyproject.toml` 固定 `fastapi==0.115.14`
- `docker-compose.yml` 加 `security_opt: seccomp=unconfined`
- `auth.py` feishu_callback 改为 `async def` + `make_session()`
- 9 个 DELETE 路由返回显式 `Response(status_code=204)`

**Why**: seccomp 是根本原因，FastAPI 版本固定是防止将来升级再出问题。

## 飞书 SSO 回调 coroutine bug（同次修复）

**问题**：服务器旧代码里 `issue_jwt` 是 `async def` 但调用时没有 `await`，导致 token 变成 `<coroutine object>` 拼入重定向 URL。
**修复**：整体同步化 + 上述 async 改造一并解决。

## PG 连接 no encryption 错误（2026-07-01）

**问题**：容器通过宿主机公网 IP `106.55.57.40` 连 PostgreSQL，pg_hba.conf 用 `scram-sha-256` 要求加密，但 psycopg3 默认非加密，报 `no pg_hba.conf entry for host ... no encryption`。

**修复**：
1. PG_DSN 加 `sslmode=disable`
2. pg_hba.conf 把 `172.16.0.0/12` 和 `106.55.57.40/32` 的认证方式改为 `md5`
3. 用 `pg_reload_conf()` 热重载（psql 路径：`/usr/local/pgsql/bin/psql`）

**Why**: pg_hba.conf `scram-sha-256` 要求 SSL，容器不走 SSL，必须改为 `md5` 或在 DSN 加 `sslmode=require`。

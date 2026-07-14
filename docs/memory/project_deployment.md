---
name: project-deployment
description: ticket-hub UAT/SIT 服务器部署细节
metadata: 
  node_type: memory
  type: project
  originSessionId: e1d618c3-a67f-419c-9a88-588833782e7f
---

## 服务器

### UAT 机器
- SSH: `rnd@rnd`（Rocky Linux 9.3，IP: 106.55.57.40）
- 项目目录: `/data/hub-issue/`，代码在 `app/backend/`
- 端口: 9094（backend）
- 访问: `http://dl.piaozone.com:18025/hub-issue/`
- docker 命令需要 `sudo`，用旧版独立 `docker-compose`
- 部署手册: `DEPLOY-UAT.md`

### SIT 机器
- SSH: `root@sit`（Ubuntu 22.04，IP: 43.139.250.182）
- 项目目录: `/data/hub-issue/`，代码在 `backend/`
- 端口: 9095（backend）
- 访问: `http://43.139.250.182/hub-issue/`
- docker compose 插件版（`docker compose`），root 用户
- 部署手册: `DEPLOY-SIT.md`

## 快速部署

**改代码 → UAT**：
```bash
rsync -av --delete backend/ rnd@rnd:/data/hub-issue/app/backend/ --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env'
ssh rnd@rnd "sudo docker restart hub-issue-backend hub-issue-worker hub-issue-worker-beat"
```

**改代码 → SIT**：
```bash
rsync -av --delete backend/ root@sit:/data/hub-issue/backend/ --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env'
ssh root@sit "cd /data/hub-issue && docker compose restart backend worker worker-beat"
```

**前端更新（两环境通用）**：
```bash
cd frontend && VITE_PUBLIC_BASE=/hub-issue/ VITE_API_BASE=/hub-issue npm run build
rsync -av --delete frontend/dist/ rnd@rnd:/data/hub-issue/frontend-dist/   # UAT
rsync -av --delete frontend/dist/ root@sit:/data/hub-issue/frontend-dist/  # SIT
```

## Docker 配置要点

- **必须加 `security_opt: seccomp=unconfined`**（Rocky Linux 9 限制线程创建）
- 镜像：UAT `hub-issue-backend:latest`，SIT `hub-issue-sit-backend:latest`
- 代码通过 volume 挂载（`./backend:/app`），改代码只需 restart 不需重建镜像
- 修改 .env 后需 `--force-recreate` 才能生效

## Nginx

- UAT：`/usr/local/nginx/conf/conf.d/download.conf`（hub-issue 段在末尾）
- SIT：`/usr/local/nginx/conf/conf.d/hub-issue.conf`（只含 location 块，不含 server 块）
- nginx 路径：`/usr/local/nginx/sbin/nginx`

## 数据库

- PostgreSQL 宿主机：`106.55.57.40:5432`，用户 `postgres`，**密码见 `deploy/.env` 的 `PG_DSN`**（gitignored）
- UAT 库：`ticket_hub_v2`；SIT 库：`ticket_hub_sit`
- pg_hba.conf：`/data/pgsql18/data/pg_hba.conf`（宿主机 UAT 上）
- reload：`PGPASSWORD=$PG_PASSWORD /usr/local/pgsql/bin/psql -h 127.0.0.1 -U postgres -c 'SELECT pg_reload_conf();'`（用 shell 变量，勿把口令写死进文档）
- PG_DSN 必须加 `sslmode=disable`（pg_hba.conf 用 md5，不加会报 no encryption）
- 已放行 IP：`172.16.0.0/12`（容器网段）、`43.139.250.182`（SIT）、`106.55.57.40`（UAT 宿主机自身）

## Redis

- UAT：容器内 `redis://hub-issue-redis:6379/0`
- SIT：外部 `redis://:<REDIS_PASSWORD>@106.55.57.40:6379/1`（**口令见 `deploy/.env` 的 `REDIS_URL`**，gitignored）

## 数据迁移

- UAT → SIT 已迁移：users（3个）、skill_prompts（7个）、skill_prompt_history
- 迁移命令：`/usr/local/pgsql18/bin/pg_dump`（pg18 版本，旧版 pg_dump 与 server 版本不兼容）

## GitHub

- Remote: `git@github.com:invagent/ticket-hub.git`（SSH）
- 本机公钥已添加到 GitHub，斌哥公钥已添加到 SIT root 账号

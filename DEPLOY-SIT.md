# SIT 环境部署手册（git 驱动 docker 部署）

> 2026-07-03 起 SIT 改为**从 GitHub git 部署**：代码烘进镜像，`git pull && up --build`
> 即更新。详细产物与回滚见 [`deploy/README.md`](deploy/README.md)。

## 环境信息

| 项目 | 值 |
|---|---|
| 服务器 | `root@43.139.250.182`（Ubuntu 22.04）|
| 访问地址 | http://43.139.250.182/hub-issue/ |
| 后端端口 | 9095（nginx `/hub-issue/` 反代）|
| git 部署根 | `/data/hub-issue/`（`invagent/ticket-hub` 的克隆）|
| 配置 | `/data/hub-issue/deploy/.env`（**不提交 git**；含 PG/Redis 口令 + 各密钥）|
| 数据库 | `106.55.57.40:5432 / ticket_hub_sit`（远程托管，口令见 `deploy/.env`）|
| Redis | `106.55.57.40:6379/1`（远程；口令见 `deploy/.env`）|
| GitHub 鉴权 | 服务器只读 deploy key（`~/.ssh/ticket-hub-deploy`，host 别名 `github-ticket-hub`）|

## 容器（`deploy/docker-compose.sit.yml`）

| 容器 | 说明 |
|---|---|
| hub-issue-sit-backend | FastAPI 后端（9095:8080）|
| hub-issue-sit-worker | Celery worker |
| hub-issue-sit-worker-beat | Celery beat 定时任务 |

> PG / Redis 是**远程托管**，不在 compose 内；本地无 pg/redis 容器。

## 日常更新

```bash
ssh root@43.139.250.182
cd /data/hub-issue && git pull

# 后端/worker/beat（改代码必须 --build，代码烘进镜像不再挂载）
docker compose -f deploy/docker-compose.sit.yml up -d --build

# 有新迁移时（作用于远程 ticket_hub_sit）
docker compose -f deploy/docker-compose.sit.yml run --rm backend alembic upgrade head

# 前端有改动时（docker 内 node 构建 → nginx 静态目录，宿主免装 node）
deploy/build-frontend.sh /data/hub-issue/frontend-dist
```

## 校验 / 日志 / 状态

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health        # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/hub-issue/health   # 200
cd /data/hub-issue && docker compose -f deploy/docker-compose.sit.yml ps
docker compose -f deploy/docker-compose.sit.yml logs backend --tail=50 -f
```

## Nginx

配置 `/usr/local/nginx/conf/conf.d/hub-issue.conf`（仓库副本：`deploy/nginx/hub-issue.conf`）。
前端静态 `alias /data/hub-issue/frontend-dist/`；构建时 `VITE_PUBLIC_BASE=/hub-issue/`。

```bash
/usr/local/nginx/sbin/nginx -t && /usr/local/nginx/sbin/nginx -s reload
```

## .env

改 `deploy/.env` 后重启即可生效：

```bash
cd /data/hub-issue && docker compose -f deploy/docker-compose.sit.yml up -d
```

- 口令/密钥一律放 `deploy/.env`（gitignored），**不要写进本文档**。
- SIT 现状：`KSM_*` 三步鉴权 + `DASHSCOPE_API_KEY`/`GLM_API_KEY` 留空 → KSM 接入与 AI 分类暂不可用，补齐对应 key 即启用。
- 飞书回调：`FEISHU_SSO_REDIRECT_URI=http://43.139.250.182/hub-issue/api/auth/feishu/callback`

## 回滚

数据在远程 PG，重建镜像不影响。需回退某次代码：`git -C /data/hub-issue checkout <sha>`
后 `docker compose -f deploy/docker-compose.sit.yml up -d --build`。

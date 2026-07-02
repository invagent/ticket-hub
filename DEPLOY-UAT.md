# UAT 环境部署手册

## 环境信息

| 项目 | 值 |
|---|---|
| 服务器 | `rnd@rnd`（Rocky Linux 9.3，IP: 106.55.57.40）|
| 访问地址 | http://dl.piaozone.com:18025/hub-issue/ |
| 后端端口 | 9094 |
| 项目目录 | `/data/hub-issue/` |
| 数据库 | `106.55.57.40:5432` / `ticket_hub_v2` / postgres:difyai123456 |
| Redis | `redis://hub-issue-redis:6379/0`（内部容器）|

## 常用部署命令

### 改代码后快速部署（不改依赖）

```bash
# 1. 同步 backend 代码
rsync -av --delete backend/ rnd@rnd:/data/hub-issue/app/backend/ \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env' --exclude='htmlcov' --exclude='.pytest_cache'

# 2. 重启服务（需要 sudo）
ssh rnd@rnd "sudo docker restart hub-issue-backend hub-issue-worker hub-issue-worker-beat"
```

### 改依赖后重建镜像

```bash
# 1. 同步代码（含 pyproject.toml）
rsync -av --delete backend/ rnd@rnd:/data/hub-issue/app/backend/ \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' --exclude='.env'

# 2. 重建镜像并重启（使用旧版 docker-compose）
ssh rnd@rnd "cd /data/hub-issue && sudo docker-compose build backend && sudo docker-compose up -d --force-recreate"
```

### 更新前端

```bash
# 1. 本地 build（注意路径配置）
cd frontend && VITE_PUBLIC_BASE=/hub-issue/ VITE_API_BASE=/hub-issue npm run build

# 2. 同步 dist
rsync -av --delete frontend/dist/ rnd@rnd:/data/hub-issue/frontend-dist/
```

### 执行数据库迁移

```bash
ssh rnd@rnd "cd /data/hub-issue && sudo docker-compose run --rm backend alembic upgrade head"
```

### 查看日志

```bash
ssh rnd@rnd "sudo docker logs hub-issue-backend --tail=50 -f"
ssh rnd@rnd "sudo docker logs hub-issue-worker --tail=50 -f"
```

### 查看容器状态

```bash
ssh rnd@rnd "sudo docker ps | grep hub-issue"
```

## 服务器目录结构

```
/data/hub-issue/
├── app/backend/      ← 后端代码（volume 挂载到容器 /app）
├── frontend-dist/    ← 前端静态文件（nginx 直接服务）
├── docker-compose.yml
└── .env              ← 生产配置（不提交 git）
```

## Nginx 配置

- 配置文件：`/usr/local/nginx/conf/conf.d/download.conf`（hub-issue 段在文件末尾）
- nginx 路径：`/usr/local/nginx/sbin/nginx`

```bash
# 检查配置
ssh rnd@rnd "sudo /usr/local/nginx/sbin/nginx -t"
# 重载配置
ssh rnd@rnd "sudo /usr/local/nginx/sbin/nginx -s reload"
```

## 容器列表

| 容器名 | 说明 |
|---|---|
| hub-issue-backend | FastAPI 后端（:9094）|
| hub-issue-worker | Celery worker |
| hub-issue-worker-beat | Celery beat 定时任务 |
| hub-issue-redis | Redis（内部使用）|

## 注意事项

- docker 命令需要 `sudo`（rnd 用户不在 docker 组）
- 使用旧版独立 `docker-compose` 命令，非 `docker compose` 插件
- PG_DSN 用 `106.55.57.40:5432`，需加 `sslmode=disable`
- `security_opt: seccomp=unconfined` 必须保留（Rocky Linux 9 seccomp 限制线程创建）
- 修改 .env 后需 `--force-recreate` 重建容器才能生效

## .env 关键配置

```
PG_DSN=postgresql+psycopg://postgres:difyai123456@106.55.57.40:5432/ticket_hub_v2?connect_timeout=10&sslmode=disable
FEISHU_SSO_REDIRECT_URI=http://dl.piaozone.com:18025/hub-issue/api/auth/feishu/callback
```

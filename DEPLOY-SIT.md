# SIT 环境部署手册

## 环境信息

| 项目 | 值 |
|---|---|
| 服务器 | `root@sit`（43.139.250.182，Ubuntu 22.04）|
| 访问地址 | http://43.139.250.182/hub-issue/ |
| 后端端口 | 9095 |
| 项目目录 | `/data/hub-issue/` |
| 数据库 | `106.55.57.40:5432` / `ticket_hub_sit` / postgres:difyai123456 |
| Redis | `106.55.57.40:6379/1` 密码 kingdee123 |

## 常用部署命令

### 改代码后快速部署（不改依赖）

```bash
# 1. 同步 backend 代码
rsync -av --delete backend/ root@sit:/data/hub-issue/backend/ \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env' --exclude='htmlcov' --exclude='.pytest_cache'

# 2. 重启服务
ssh root@sit "cd /data/hub-issue && docker compose restart backend worker worker-beat"
```

### 改依赖后重建镜像

```bash
# 1. 同步代码（含 pyproject.toml）
rsync -av --delete backend/ root@sit:/data/hub-issue/backend/ \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env'

# 2. 重建镜像并重启
ssh root@sit "cd /data/hub-issue && docker compose build backend && docker compose up -d --force-recreate"
```

### 更新前端

```bash
# 1. 本地 build
cd frontend && VITE_PUBLIC_BASE=/hub-issue/ VITE_API_BASE=/hub-issue npm run build

# 2. 同步 dist
rsync -av --delete frontend/dist/ root@sit:/data/hub-issue/frontend-dist/
```

### 执行数据库迁移

```bash
ssh root@sit "cd /data/hub-issue && docker compose run --rm backend alembic upgrade head"
```

### 查看日志

```bash
ssh root@sit "cd /data/hub-issue && docker compose logs backend --tail=50 -f"
ssh root@sit "cd /data/hub-issue && docker compose logs worker --tail=50 -f"
```

### 查看容器状态

```bash
ssh root@sit "cd /data/hub-issue && docker compose ps"
```

## 服务器目录结构

```
/data/hub-issue/
├── backend/          ← 后端代码（volume 挂载到容器 /app）
├── frontend-dist/    ← 前端静态文件（nginx 直接服务）
├── docker-compose.yml
└── .env              ← 生产配置（不提交 git）
```

## Nginx 配置

配置文件：`/usr/local/nginx/conf/conf.d/hub-issue.conf`

```bash
# 检查配置
ssh root@sit "/usr/local/nginx/sbin/nginx -t"
# 重载配置
ssh root@sit "/usr/local/nginx/sbin/nginx -s reload"
```

## 容器列表

| 容器名 | 说明 |
|---|---|
| hub-issue-sit-backend | FastAPI 后端（:9095）|
| hub-issue-sit-worker | Celery worker |
| hub-issue-sit-worker-beat | Celery beat 定时任务 |
| hub-issue-sit-redis | Redis（内部使用）|

## .env 关键配置

修改 .env 后需 `--force-recreate` 重建容器：

```bash
ssh root@sit "cd /data/hub-issue && docker compose up -d --force-recreate"
```

主要配置项位于 `/data/hub-issue/.env`，飞书回调地址：
```
FEISHU_SSO_REDIRECT_URI=http://43.139.250.182/hub-issue/api/auth/feishu/callback
```

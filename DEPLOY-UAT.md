# UAT 环境部署手册

> **更新时间**：2026-09-11  
> **安装目录**：`/data/ticket-hub-uat/`  
> **架构要点**：后端采用源码卷挂载（`/data/ticket-hub-uat/backend:/app`），更新代码同步后直接重启服务即可生效，无需重新构建镜像；前端静态产物由宿主机 Nginx 直接托管。

---

## 一、环境信息

| 项目 | 值 | 说明 |
|---|---|---|
| 服务器 | `rnd@106.55.57.40`（Rocky Linux 9.3） | 默认端口 22 |
| 访问地址 | `http://dl.piaozone.com:18025/ticket-hub-uat/` | 公网域名及入口路径 |
| 飞书回调 | `http://dl.piaozone.com:18025/ticket-hub-uat/api/auth/feishu/callback` | 飞书开放平台登录重定向地址 |
| 后端端口 | `127.0.0.1:19095`（host 网络模式） | Nginx 反向代理端口 |
| 项目目录 | `/data/ticket-hub-uat/` | 包含 backend、frontend-dist、deploy 等 |
| 数据库 | `127.0.0.1:5432` / `ticket-hub-uat` | 本机 PG18，应用账号 `ticket_hub_uat_app` |
| Redis | `127.0.0.1:6379/2`（DB 2） | 宿主机 Redis，密码见 `deploy/.env` |
| MinIO | `127.0.0.1:9000`，桶 `ticket-hub-uat` | 容器 `uat-minio`，数据目录 `/data/minio/data` |
| 配置文件 | `/data/ticket-hub-uat/deploy/.env` | 权限 600（所属用户 `rnd`） |
| Compose 文件 | `/data/ticket-hub-uat/deploy/docker-compose.uat.yml` | 统一管理 backend/worker/worker-beat |

---

## 二、常用部署流程

### 1. 更新后端代码（日常改动）

后端已配置源码挂载卷 `/data/ticket-hub-uat/backend:/app`，同步代码后重启容器即可生效：

```bash
# 步骤 1：同步 backend 源码到 UAT 目录（注意过滤缓存与本地虚拟环境）
rsync -av --delete backend/ rnd@106.55.57.40:/data/ticket-hub-uat/backend/ \
  --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.env*' --exclude='htmlcov' --exclude='.pytest_cache' \
  --exclude='.mypy_cache' --exclude='.ruff_cache'

# 步骤 2：重启 UAT 容器服务（包含 worker-beat 定时调度）
ssh rnd@106.55.57.40 "cd /data/ticket-hub-uat/deploy && sudo docker-compose -f docker-compose.uat.yml --profile automation up -d --force-recreate"
```

### 2. 更新前端页面

> **注意**：UAT 访问路径前缀为 `/ticket-hub-uat/`，构建时必须指定对应的 base 环境变量，否则会导致静态资源加载 404 或白屏。

```bash
# 步骤 1：本地执行带 UAT 路径的前端构建
cd frontend && VITE_PUBLIC_BASE=/ticket-hub-uat/ VITE_API_BASE=/ticket-hub-uat npm run build

# 步骤 2：同步构建产物到 UAT Nginx 静态托管目录
rsync -av --delete dist/ rnd@106.55.57.40:/data/ticket-hub-uat/frontend-dist/
```

*（若本地与 UAT 之间通过 SIT 中转，可将 `dist/` 打包传输至 UAT 的 `/data/ticket-hub-uat/frontend-dist/` 解压）*

### 3. 执行数据库迁移（Alembic）

当后端增加了新的数据库迁移脚本（`migrations/versions/`）时执行：

```bash
ssh rnd@106.55.57.40 "sudo docker exec ticket-hub-uat-backend alembic upgrade head"
```

---

## 三、服务检查与日常运维

### 1. 检查健康状态

```bash
# UAT 后端 Readiness 探针（包含数据库连接检查，期望 HTTP 200 且 status="ready"）
curl -s http://127.0.0.1:19095/health/ready

# 外部公网健康检查
curl -s http://dl.piaozone.com:18025/ticket-hub-uat/health
```

### 2. 查看容器状态

```bash
ssh rnd@106.55.57.40 "sudo docker ps | grep ticket-hub-uat"
```

当前正常运行的容器清单：

| 容器名 | 镜像 | 职责说明 |
|---|---|---|
| `ticket-hub-uat-backend` | `ticket-hub-uat-backend:f70af5a` | FastAPI 后端服务（端口 19095） |
| `ticket-hub-uat-worker` | `ticket-hub-uat-backend:f70af5a` | Celery 异步任务消费 Worker（并发 2） |
| `ticket-hub-uat-worker-beat` | `ticket-hub-uat-backend:f70af5a` | Celery Beat 定时调度（每 2min 消费回写与状态流转） |
| `uat-minio` | `minio/minio` | 附件对象存储服务 |

### 3. 查看服务日志

```bash
# 查看后端 API 访问与业务日志
ssh rnd@106.55.57.40 "sudo docker logs --tail=100 -f ticket-hub-uat-backend"

# 查看异步任务 Worker 执行日志（如出站回写、AI 作答等）
ssh rnd@106.55.57.40 "sudo docker logs --tail=100 -f ticket-hub-uat-worker"

# 查看定时调度 Beat 执行日志
ssh rnd@106.55.57.40 "sudo docker logs --tail=100 -f ticket-hub-uat-worker-beat"
```

---

## 四、服务器配置细节

### 1. 目录结构

```
/data/ticket-hub-uat/
├── backend/                  ← 后端源码目录（挂载至容器 /app）
├── frontend-dist/            ← 前端静态文件（Nginx 托管根目录）
├── deploy/
│   ├── .env                  ← 运行时环境变量（权限 600）
│   ├── docker-compose.uat.yml← Compose 服务编排文件
│   └── seccomp-compat.json   ← Rocky Linux 9 线程创建 seccomp 配置
└── UAT-部署记录.md           ← 历史迁移与重新同步记录
```

### 2. Nginx 配置

Nginx 配置片段位于：`/usr/local/nginx/conf/snippets/ticket-hub-uat.locations.conf`

```nginx
location = /ticket-hub-uat {
    return 301 /ticket-hub-uat/;
}
location ^~ /ticket-hub-uat/api/ {
    proxy_pass http://127.0.0.1:19095/api/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 300s;
    client_max_body_size 20m;
}
location ^~ /ticket-hub-uat/webhook/ {
    proxy_pass http://127.0.0.1:19095/webhook/;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 120s;
}
location ^~ /ticket-hub-uat/health {
    proxy_pass http://127.0.0.1:19095/health;
    proxy_set_header Host $http_host;
}
location /ticket-hub-uat/ {
    alias /data/ticket-hub-uat/frontend-dist/;
    try_files $uri $uri/ /ticket-hub-uat/index.html;
    index index.html;
}
```

重载 Nginx 命令：
```bash
ssh rnd@106.55.57.40 "sudo /usr/local/nginx/sbin/nginx -t && sudo /usr/local/nginx/sbin/nginx -s reload"
```

### 3. 注意事项

1. **Docker 权限**：`rnd` 用户执行 docker 与 docker-compose 命令需带 `sudo`。
2. **Compose 传参**：启动 Worker 与 Beat 时必须携带 `--profile automation` 参数，否则只会启动 backend。
3. **Seccomp 配置**：Rocky Linux 9 限制容器内系统线程创建，所有服务均需保留 `seccomp=/data/ticket-hub-uat/deploy/seccomp-compat.json`。
4. **自动化开关与 Dry Run**：已与 SIT 完全对齐（`KSM_AUTO_TAKEOVER_ENABLED=true`、`KSM_WRITEBACK_ENABLED=true`、`KSM_WRITEBACK_DRY_RUN=false`、`OPERATION_AUTO_REPLY_ENABLED=true` 等），确保出站操作与定时任务正常自动执行。

# hub-issue SIT — git-driven docker 部署

把服务器（`43.139.250.182`）的 ticket-hub SIT 栈从 rsync + 代码挂载改成
**从 GitHub 仓库 git 部署**：代码烘进镜像，`git pull && up --build` 即更新。

范围：ticket-hub 栈（backend / worker / worker-beat）。`dify-forward`（飞书↔Dify
AI 客服桥）不在此列，独立维护。

## 拓扑

```
nginx(:80, /usr/local/nginx) ──/hub-issue/api/──▶ 127.0.0.1:9095 ─▶ backend:8080
                             └─/hub-issue/（静态）▶ /data/hub-issue/frontend-dist
docker compose (deploy/docker-compose.sit.yml)
  backend      hub-issue-sit-backend    9095:8080
  worker       celery worker -Q celery
  worker-beat  celery beat
PG + Redis     远程 106.55.57.40（ticket_hub_sit / redis db1）—— 不在 compose 内
```

## 产物（本目录）

| 文件 | 作用 |
|---|---|
| `backend.Dockerfile` | 后端镜像（context = 仓库根，COPY `backend/`）|
| `docker-compose.sit.yml` | backend/worker/beat 编排（无本地 pg/redis）|
| `build-frontend.sh` | docker 内 node 构建前端 → `frontend-dist`（宿主免装 node）|
| `nginx/hub-issue.conf` | nginx location（装到 `/usr/local/nginx/conf/conf.d/`）|
| `.env.sit.example` | env 模板（真值放 `deploy/.env`，gitignored）|

## 首次部署

```bash
# 0) 服务器上加只读 deploy key（一次）
ssh-keygen -t ed25519 -C "hub-issue-sit-deploy" -f ~/.ssh/ticket-hub-deploy -N ""
cat >> ~/.ssh/config <<'EOF'
Host github-ticket-hub
  HostName github.com
  User git
  IdentityFile ~/.ssh/ticket-hub-deploy
  IdentitiesOnly yes
EOF
cat ~/.ssh/ticket-hub-deploy.pub   # ← 贴到 GitHub 仓库 Settings → Deploy keys（只读）

# 1) 备份旧部署 + 保住 .env
cp -a /data/hub-issue /data/hub-issue.bak.$(date +%Y%m%d)   # 若旧目录存在
cp /data/hub-issue/.env /root/hub-issue.env.bak             # 保住现有 .env

# 2) clone（git 部署根：/data/hub-issue-git）
git clone github-ticket-hub:invagent/ticket-hub.git /data/hub-issue-git
cd /data/hub-issue-git

# 3) 放 .env（沿用旧的；无则从模板起）
cp /root/hub-issue.env.bak deploy/.env        # 或 cp deploy/.env.sit.example deploy/.env && vim

# 4) 迁移（远程库；已在 0016 则幂等空跑）
docker compose -f deploy/docker-compose.sit.yml build backend
docker compose -f deploy/docker-compose.sit.yml run --rm backend alembic upgrade head

# 5) 起栈
docker compose -f deploy/docker-compose.sit.yml up -d --build

# 6) 前端 → nginx 静态目录
deploy/build-frontend.sh /data/hub-issue/frontend-dist

# 7) nginx location（如已存在同名可跳过）
cp deploy/nginx/hub-issue.conf /usr/local/nginx/conf/conf.d/hub-issue.conf
nginx -t && nginx -s reload

# 8) 校验
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9095/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/hub-issue/health
```

## 日常更新

```bash
cd /data/hub-issue-git && git pull
docker compose -f deploy/docker-compose.sit.yml up -d --build    # 后端/worker/beat
docker compose -f deploy/docker-compose.sit.yml run --rm backend alembic upgrade head  # 有新迁移时
deploy/build-frontend.sh /data/hub-issue/frontend-dist           # 前端有改动时
```

## 回滚

旧栈目录保留在 `/data/hub-issue.bak.<date>`；如需回退：
`docker compose -f deploy/docker-compose.sit.yml down` 后 `cd /data/hub-issue && docker compose up -d`。
数据在远程 PG，不受镜像重建影响。

## 注意

- `.env` 永不进 git（`.env*` 已忽略）。KSM 三步鉴权变量在 SIT 目前留空 → 回写自动跳过。
- 代码烘进镜像（不再挂载 `./backend`），所以改代码必须 `--build`。
- PG/Redis 是远程托管，`down`/`up` 不动数据；`alembic upgrade` 作用于远程 `ticket_hub_sit`。

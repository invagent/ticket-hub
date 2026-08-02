# 飞书二季度工单导入 SIT 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 SIT 库 mock 业务数据，把飞书导出的 2026 二季度 5888 条工单直接建库行导入。

**Architecture:** 一次性 Python 脚本（`scripts/import_feishu_q2.py`）读三个 CSV → 映射字段 → 批量建 `tickets` 行（type=Raw，不跑 AI/路由/毕业）。清库用一段有序 DELETE SQL（先备份 pg_dump）。脚本在 SIT `backend/` 目录下用 `.venv/bin/python3.12 ../scripts/import_feishu_q2.py` 运行，沿用 `migrate_ksm_reporter.py` 的 `init_engine()`+`get_session()` 模式。

**Tech Stack:** Python 3.12、SQLAlchemy ORM、csv.DictReader、PostgreSQL（SIT `ticket_hub_sit`）。

## Global Constraints

- 脚本从 `backend/` 目录运行，`sys.path.insert(0, ".")`，import `app.db` / `app.models`（同 `migrate_ksm_reporter.py`）
- CSV 读取用 `encoding="utf-8-sig"`（去 BOM）
- 幂等键：`tickets.source_ticket_id`（= CSV 工单ID，如 FPY2026032300292）；已存在跳过
- 时间解析：CSV `2026/04/01 13:22` 无时区，按北京时间 `+08:00` 入库
- `type='Raw'` 约束（ck_tickets_type_fields）：必须 `source_code IS NOT NULL AND source_ticket_id IS NOT NULL AND internal_split_id IS NULL`
- `predicted_type` 只能是 `Operation/Bug_fix/Demand/Internal_task`（ck_tickets_predicted_type）
- 数据源真值：SIT 库 `ticket_hub_sit` @ 106.55.57.40，SSH `root@sit`
- 不跑 AI/Router/hub 毕业/Linear/outbox/身份图谱（见设计 §4.4）
- CSV 文件：`docs/发票云工单管理_（新）发票云工单列表_表格-{4,5,6}月.csv`（4月1994/5月1765/6月2129 = 5888 条）
- 参考设计文档：`docs/superpowers/specs/2026-08-02-feishu-q2-import-design.md`

---

## 阶段一：清理 mock 数据

### Task 1: 备份 SIT 库

**Files:** 无（运维操作）

**Interfaces:**
- Produces: SIT 上 `/data/hub-issue/backup/ticket_hub_sit_<ts>.sql` 备份文件（唯一回滚点）

- [ ] **Step 1: 记录清理前各表行数**

在 SIT 上执行，保存输出到本地记录：
```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -tAc \"SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;\""
```
Expected: tickets=31, hub_issues=16, agent_decisions=44, status_history=97, customers=12, customer_identities=14, sync_outbox=14, ticket_embeddings=2, ticket_hub_issue_history=15, hub_issue_reply_history=5 等。

- [ ] **Step 2: pg_dump 全库备份**

```bash
ssh root@sit "mkdir -p /data/hub-issue/backup && PGPASSWORD=difyai123456 /usr/local/pgsql18/bin/pg_dump -h 106.55.57.40 -U postgres -d ticket_hub_sit -f /data/hub-issue/backup/ticket_hub_sit_$(date +%Y%m%d-%H%M%S).sql"
```
Expected: 命令成功，备份文件生成。

- [ ] **Step 3: 验证备份文件非空**

```bash
ssh root@sit "ls -la /data/hub-issue/backup/ && tail -3 /data/hub-issue/backup/ticket_hub_sit_*.sql"
```
Expected: 文件大小 > 0，末尾有 `PostgreSQL database dump complete`。

### Task 2: 清理业务流水数据

**Files:**
- Create: `scripts/cleanup_sit_mock.sql`（清理 SQL，供审阅与重复执行）

**Interfaces:**
- Consumes: Task 1 的备份（回滚点）
- Produces: 清空的业务表；保留基础配置表

- [ ] **Step 1: 写清理 SQL**

Create `scripts/cleanup_sit_mock.sql`：
```sql
-- 清理 SIT mock 业务流水数据（保留 users/skill_prompts/分工/system_settings/sources/产品线目录）
-- 执行前必须已 pg_dump 备份。单事务，先子后父。
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
```

说明：`materialized_metrics`(31) 是 mock 工单物化出的指标，一并清；`assignment_scope_history`(1) 属分工配置审计，**保留**不清。

- [ ] **Step 2: dry-run 检查（BEGIN...ROLLBACK 试跑）**

先用 ROLLBACK 版本验证 SQL 无语法/FK 错误：
```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -c \"BEGIN; UPDATE tickets SET hub_issue_id=NULL; DELETE FROM agent_decisions; DELETE FROM status_history; DELETE FROM ticket_hub_issue_history; DELETE FROM hub_issue_reply_history; DELETE FROM hub_issue_linear_issues; DELETE FROM hub_issue_relations; DELETE FROM sync_outbox; DELETE FROM attachments; DELETE FROM ticket_embeddings; DELETE FROM notification_log; DELETE FROM customer_merge_history; DELETE FROM materialized_metrics; DELETE FROM tickets; DELETE FROM hub_issues; DELETE FROM customer_identities; DELETE FROM customers; ROLLBACK;\""
```
Expected: 一串 `DELETE N` / `UPDATE N` 输出，无 ERROR，最后 ROLLBACK。若报 FK 错 → 补充依赖表到删除序列。

- [ ] **Step 3: 正式执行清理**

把 `scripts/cleanup_sit_mock.sql` 拷到 SIT 执行（或直接内联 COMMIT 版）：
```bash
scp scripts/cleanup_sit_mock.sql root@sit:/tmp/
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -f /tmp/cleanup_sit_mock.sql"
```
Expected: 全部 DELETE 成功，最后 COMMIT。

- [ ] **Step 4: 验证清理结果 + 保留表未动**

```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -tAc \"SELECT 'tickets',count(*) FROM tickets UNION ALL SELECT 'hub_issues',count(*) FROM hub_issues UNION ALL SELECT 'customers',count(*) FROM customers UNION ALL SELECT 'users',count(*) FROM users UNION ALL SELECT 'skill_prompts',count(*) FROM skill_prompts UNION ALL SELECT 'sources',count(*) FROM sources UNION ALL SELECT 'product_lines',count(*) FROM product_lines UNION ALL SELECT 'system_settings',count(*) FROM system_settings UNION ALL SELECT 'assignment_scopes_module',count(*) FROM assignment_scopes_module;\""
```
Expected: tickets/hub_issues/customers = 0；users=99, skill_prompts=7, sources=5, product_lines=5, system_settings=1, assignment_scopes_module=1（**基础配置全保留**）。

- [ ] **Step 5: Commit 清理 SQL**

```bash
cd /Users/junill/Documents/04_claude/01_ticket/hub-issue
git add scripts/cleanup_sit_mock.sql
git commit -m "chore(sit): 清理 mock 业务数据 SQL"
```

---

## 阶段二：导入脚本

### Task 3: 映射函数（纯函数，本地可测）

**Files:**
- Create: `scripts/import_feishu_q2.py`（先只写映射纯函数 + 单元自测块）

**Interfaces:**
- Produces:
  - `map_source(工单来源: str) -> str` — 返回 `ksm`/`zhichi`/`feishu`
  - `map_predicted_type(提单类型: str) -> str` — 返回 `Operation`/`Bug_fix`/`Demand`
  - `map_status(工单状态: str) -> str` — 返回 ticket status
  - `parse_dt(s: str) -> datetime` — 解析 `2026/04/01 13:22` 为 +08:00 aware datetime
  - `build_body(问题描述: str, 处理过程: str) -> str`

- [ ] **Step 1: 写映射函数 + 断言自测**

Create `scripts/import_feishu_q2.py`（先只放这段，末尾 `if __name__ == "__main__"` 里先放自测）：
```python
"""导入飞书二季度工单到 SIT（一次性脚本）。

用法（在 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/import_feishu_q2.py --dry-run
    .venv/bin/python3.12 ../scripts/import_feishu_q2.py            # 正式写库
CSV 路径通过 --csv-dir 指定（默认 ../docs）。
"""
from __future__ import annotations

import argparse
import csv
import glob
import sys
from datetime import UTC, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

_SOURCE_MAP = {"KSM": "ksm", "智齿": "zhichi", "多维表格（内部）": "feishu"}
_TYPE_MAP = {"需求": "Demand", "BUG": "Bug_fix"}
_STATUS_MAP = {
    "处理完成": "done",
    "退回KSM处理": "done",
    "已退回": "done",
    "处理中": "in_progress",
    "升级产研处理": "in_progress",
    "处理关闭": "closed",
    "待处理": "received",
}


def map_source(v: str) -> str:
    return _SOURCE_MAP.get((v or "").strip(), "feishu")


def map_predicted_type(v: str) -> str:
    return _TYPE_MAP.get((v or "").strip(), "Operation")


def map_status(v: str) -> str:
    return _STATUS_MAP.get((v or "").strip(), "received")


def parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y/%m/%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=CST)
        except ValueError:
            continue
    return None


def build_body(desc: str, process: str) -> str:
    parts = []
    if (desc or "").strip():
        parts.append(desc.strip())
    if (process or "").strip():
        parts.append(f"\n--- 处理过程 ---\n{process.strip()}")
    return "\n".join(parts).strip()


def _selftest() -> None:
    assert map_source("KSM") == "ksm"
    assert map_source("智齿") == "zhichi"
    assert map_source("多维表格（内部）") == "feishu"
    assert map_source("") == "feishu"
    assert map_predicted_type("需求") == "Demand"
    assert map_predicted_type("BUG") == "Bug_fix"
    assert map_predicted_type("") == "Operation"
    assert map_predicted_type("应用咨询") == "Operation"
    assert map_status("处理完成") == "done"
    assert map_status("退回KSM处理") == "done"
    assert map_status("处理中") == "in_progress"
    assert map_status("") == "received"
    dt = parse_dt("2026/04/01 13:22")
    assert dt is not None and dt.year == 2026 and dt.hour == 13
    assert dt.utcoffset() == timedelta(hours=8)
    assert parse_dt("") is None
    assert "处理过程" in build_body("问题", "步骤")
    assert build_body("问题", "") == "问题"
    print("selftest OK")


if __name__ == "__main__":
    _selftest()
```

- [ ] **Step 2: 运行自测块，验证映射正确**

Run: `cd backend && python3 ../scripts/import_feishu_q2.py`
Expected: 打印 `selftest OK`，无 AssertionError。

- [ ] **Step 3: Commit**

```bash
git add scripts/import_feishu_q2.py
git commit -m "feat(import): 飞书工单导入映射函数 + 自测"
```

### Task 4: CSV 解析 + 处理人匹配（dry-run 统计）

**Files:**
- Modify: `scripts/import_feishu_q2.py`（加 CSV 加载、处理人映射、dry-run 统计）

**Interfaces:**
- Consumes: Task 3 的映射函数
- Produces:
  - `load_rows(csv_dir: str) -> list[dict]` — 合并三个 CSV 的所有行
  - `build_handler_index(db) -> dict[str, int]` — 中文名→user_id（重名取有 email 的）
  - `main(dry_run, csv_dir)` — dry-run 打印统计不写库

- [ ] **Step 1: 加 CSV 加载与 dry-run 统计**

在 `_selftest` 之后、`__main__` 之前插入。同时把 `__main__` 改成走 argparse：
```python
_CSV_GLOB = "发票云工单管理_*工单列表_表格-*月.csv"


def load_rows(csv_dir: str) -> list[dict]:
    rows: list[dict] = []
    files = sorted(glob.glob(f"{csv_dir}/{_CSV_GLOB}"))
    if not files:
        raise SystemExit(f"未找到 CSV：{csv_dir}/{_CSV_GLOB}")
    for f in files:
        with open(f, encoding="utf-8-sig") as fh:
            rows.extend(csv.DictReader(fh))
    return rows


def build_handler_index(db) -> dict[str, int]:
    """中文名 → user_id。重名优先取有 email 的（真实在用账号）。"""
    from app.models import User

    idx: dict[str, int] = {}
    users = db.execute(_select_users()).scalars().all()
    for u in sorted(users, key=lambda x: (x.email or "") == ""):  # 有 email 的先入，后写覆盖
        idx[u.name] = u.id
    # 反转：让有 email 的最终生效
    idx = {}
    for u in users:
        if u.name not in idx or (u.email and not _has_email(users, idx[u.name])):
            idx[u.name] = u.id
    return idx
```

改用更简单可靠的重名处理，替换上面 `build_handler_index` 为：
```python
def build_handler_index(db) -> dict[str, int]:
    """中文名 → user_id。重名优先取有 email 的（真实在用账号）。"""
    from app.models import User
    from sqlalchemy import select

    users = db.execute(select(User).where(User.deleted_at.is_(None))).scalars().all()
    idx: dict[str, int] = {}
    for u in users:
        prev = idx.get(u.name)
        if prev is None:
            idx[u.name] = u.id
        else:
            # 已有同名：若当前 user 有 email 而占位的没有，则替换
            prev_user = next(x for x in users if x.id == prev)
            if (u.email or "") and not (prev_user.email or ""):
                idx[u.name] = u.id
    return idx


def _stats(rows: list[dict], handler_idx: dict[str, int]) -> None:
    from collections import Counter

    src = Counter(map_source(r.get("工单来源", "")) for r in rows)
    typ = Counter(map_predicted_type(r.get("提单类型", "")) for r in rows)
    sta = Counter(map_status(r.get("工单状态", "")) for r in rows)
    matched = sum(
        1 for r in rows if (r.get("处理人 (人员 )") or "").strip() in handler_idx
    )
    print(f"总行数: {len(rows)}")
    print(f"source 分布: {dict(src)}")
    print(f"predicted_type 分布: {dict(typ)}")
    print(f"status 分布: {dict(sta)}")
    print(f"处理人可匹配行: {matched} / {len(rows)}")
```

替换 `__main__`：
```python
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--csv-dir", default="../docs")
    args = ap.parse_args()

    _selftest()
    rows = load_rows(args.csv_dir)

    sys.path.insert(0, ".")
    from app.db import get_session, init_engine

    init_engine()
    db = next(get_session())
    try:
        handler_idx = build_handler_index(db)
        if args.dry_run:
            _stats(rows, handler_idx)
            return
        _import(db, rows, handler_idx)  # Task 5 实现
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 本地 dry-run 验证统计**

Run: `cd backend && python3 ../scripts/import_feishu_q2.py --dry-run --csv-dir ../docs`

注：本地无 SIT DB 连接时，`build_handler_index` 会连库失败。若本地不便连库，改在 SIT 上跑此步。本地仅验证 CSV 解析：临时把 `--dry-run` 的 handler_idx 传空 dict 亦可。
Expected: 总行数 5888；source 分布 ksm~5105/zhichi~616/feishu~167；predicted_type Demand~339/Bug_fix~114/Operation~5435；status done~5567/in_progress~185/closed~60/received~76。

- [ ] **Step 3: Commit**

```bash
git add scripts/import_feishu_q2.py
git commit -m "feat(import): CSV 加载 + 处理人索引 + dry-run 统计"
```

### Task 5: 建库行写入（幂等、分批）

**Files:**
- Modify: `scripts/import_feishu_q2.py`（加 `_import` 写库函数 + catalog upsert）

**Interfaces:**
- Consumes: Task 3/4 的映射函数、`build_handler_index`
- Produces: `_import(db, rows, handler_idx)` — 幂等建 tickets 行

- [ ] **Step 1: 写 _import 函数**

在 `_stats` 后插入：
```python
def _next_short_code_base(db) -> int:
    """现有 tickets 最大序号，导入从此 +1 递增。"""
    from sqlalchemy import func, select

    from app.models import Ticket

    n = db.execute(select(func.count(Ticket.id))).scalar() or 0
    return n


def _existing_source_ids(db) -> set[str]:
    from sqlalchemy import select

    from app.models import Ticket

    return set(
        db.execute(select(Ticket.source_ticket_id).where(Ticket.source_ticket_id.is_not(None)))
        .scalars()
        .all()
    )


def _upsert_catalog(db, product_line: str, module: str) -> None:
    """产品线/模块无则建（同入库 catalog_upsert 逻辑，ON CONFLICT DO NOTHING）。"""
    from sqlalchemy import text

    pl = (product_line or "").strip()
    if pl:
        db.execute(
            text(
                "INSERT INTO product_lines (code, name) VALUES (:c, :c) "
                "ON CONFLICT (code) DO NOTHING"
            ),
            {"c": pl[:64]},
        )


def _import(db, rows: list[dict], handler_idx: dict[str, int]) -> None:
    from app.models import Ticket

    existing = _existing_source_ids(db)
    seq = _next_short_code_base(db)
    created = skipped = 0

    for i, r in enumerate(rows):
        sid = (r.get("工单ID") or "").strip()
        if not sid or sid in existing:
            skipped += 1
            continue
        existing.add(sid)
        seq += 1

        pl = (r.get("产品线") or "").strip() or None
        if pl:
            _upsert_catalog(db, pl, "")

        handler = (r.get("处理人 (人员 )") or "").strip()
        dt = parse_dt(r.get("工单创建时间", ""))

        t = Ticket(
            short_code=f"TKT-{seq:06d}",
            source_code=map_source(r.get("工单来源", "")),
            source_ticket_id=sid,
            type="Raw",
            title=(r.get("主题") or "").strip()[:512] or None,
            body=build_body(r.get("问题描述", ""), r.get("工单处理过程", "")) or None,
            product_line_code=pl,
            module=(r.get("产品模块") or "").strip()[:128] or None,
            status=map_status(r.get("工单状态", "")),
            source_status=(r.get("工单状态") or "").strip()[:64] or None,
            predicted_type=map_predicted_type(r.get("提单类型", "")),
            predicted_confidence=None,
            assigned_user_id=handler_idx.get(handler),
            reporter={
                "feedback_user": (r.get("反馈人") or "").strip() or None,
                "mobile": (r.get("反馈人手机") or "").strip() or None,
                "tel": (r.get("反馈人电话") or "").strip() or None,
                "email": (r.get("反馈人邮箱") or "").strip() or None,
            },
            received_at=dt,
            created_at=dt,
            source_payload={"_feishu_import": r},
        )
        db.add(t)
        created += 1
        if created % 500 == 0:
            db.commit()
            print(f"  已提交 {created} 条...")

    db.commit()
    print(f"导入完成：created={created} skipped={skipped}")
```

注：`received_at`/`created_at` 若 `dt` 为 None，DB server_default 会填 now()——但 ORM 显式传 None 会覆盖默认。对空时间的行，改为不传该字段。修正：`dt` 为 None 时用 `db.execute(text(...))` 不含时间列，或先过滤。简化处理——空时间极少（工单创建时间基本都有值），dt=None 时回退当前时间：
```python
        from datetime import datetime as _dtnow
        dt = parse_dt(r.get("工单创建时间", "")) or _dtnow.now(CST)
```
用上面这行替换 `_import` 里的 `dt = parse_dt(...)`。

- [ ] **Step 2: SIT 上 dry-run 复核（连真实库统计）**

先把脚本同步到 SIT（git push + pull，或 scp）：
```bash
git add scripts/import_feishu_q2.py && git commit -m "feat(import): 建库行写入逻辑" && git push
ssh root@sit "cd /data/hub-issue && git pull"
```
在 SIT backend 容器/venv 跑 dry-run：
```bash
ssh root@sit "docker exec hub-issue-sit-backend python3 /data/hub-issue/scripts/import_feishu_q2.py --dry-run --csv-dir /data/hub-issue/docs"
```
（若 docs/CSV 未随 git 同步，先 scp 三个 CSV 到 SIT `/data/hub-issue/docs/`）
Expected: 打印总行数 5888 + 各分布 + 处理人可匹配 ~4797。

- [ ] **Step 3: Commit（若上一步未提交）**

已在 Step 2 提交。

### Task 6: 正式导入 + 验证

**Files:** 无（执行 + 验证）

**Interfaces:**
- Consumes: Task 2 已清库、Task 5 脚本就绪

- [ ] **Step 1: 正式导入**

```bash
ssh root@sit "docker exec hub-issue-sit-backend python3 /data/hub-issue/scripts/import_feishu_q2.py --csv-dir /data/hub-issue/docs"
```
Expected: 分批打印"已提交 N 条"，最后 `导入完成：created≈5888 skipped=0`。

- [ ] **Step 2: 验证行数与分布**

```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -tAc \"SELECT 'total',count(*) FROM tickets UNION ALL SELECT 'src_'||source_code,count(*) FROM tickets GROUP BY source_code UNION ALL SELECT 'type_'||predicted_type,count(*) FROM tickets GROUP BY predicted_type UNION ALL SELECT 'st_'||status,count(*) FROM tickets GROUP BY status UNION ALL SELECT 'assigned_notnull',count(*) FROM tickets WHERE assigned_user_id IS NOT NULL;\""
```
Expected: total≈5888；src_ksm~5105/src_zhichi~616/src_feishu~167；type_Demand~339/type_Bug_fix~114/type_Operation~5435；assigned_notnull~4797。

- [ ] **Step 3: 抽查数据质量**

```bash
ssh root@sit "PGPASSWORD=difyai123456 psql -h 106.55.57.40 -U postgres -d ticket_hub_sit -c \"SELECT short_code, source_code, source_ticket_id, predicted_type, status, received_at, title FROM tickets ORDER BY id DESC LIMIT 5;\""
```
Expected: short_code 形如 TKT-0000NN 连续唯一；source_ticket_id 为 FPY...；received_at 有正确时间；title 非空。

- [ ] **Step 4: 前端验证**

访问 `http://43.139.250.182/hub-issue/` 工单列表页，确认能翻页、显示 AI 分类标签、处理人名。
Expected: 列表正常渲染这批工单。

- [ ] **Step 5: 记录到 memory**

更新 memory：SIT 已清 mock 并导入飞书二季度 5888 条历史工单（直接建库行，未跑 AI）。

## Self-Review

- **Spec 覆盖**：清理边界（§3）→Task 1-2；字段映射（§4.2）→Task 3,5；状态映射（§5）→Task 3；处理人匹配（§4.3）→Task 4；不做的事（§4.4）→脚本不含相关调用；验证（§6）→Task 6。全覆盖。
- **占位符**：无 TBD；空时间处理已在 Task 5 Step 1 明确修正为回退当前时间。
- **类型一致**：`map_source/map_predicted_type/map_status/parse_dt/build_body` 在 Task 3 定义，Task 5 一致引用；`build_handler_index`/`_import` 签名一致。
- **约束合规**：type=Raw 满足 ck_tickets_type_fields；predicted_type ∈ 四类合法值。

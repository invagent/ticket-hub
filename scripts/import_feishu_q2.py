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
from datetime import datetime, timedelta, timezone

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
    from sqlalchemy import select

    from app.models import User

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

    for r in rows:
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
        dt = parse_dt(r.get("工单创建时间", "")) or datetime.now(CST)

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
        _import(db, rows, handler_idx)
    finally:
        db.close()


if __name__ == "__main__":
    main()

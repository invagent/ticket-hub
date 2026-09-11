"""回填存量 KSM 工单的 service_level（服务等级）。

从 source_payload._subscribe_callback.customerInfo.serviceLevel 提取服务等级 code，
经 sla_levels 数据库表与兜底字典映射为中文名称，写回 tickets.service_level。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/backfill_ksm_service_level.py [--dry-run] [--days N] [--all]
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, ".")
from sqlalchemy import or_, select

from app.db import get_session, init_engine
from app.models import SlaLevel, Ticket
from app.services.ingest.ksm_ingester import _KSM_SLA_FALLBACK


def _extract_service_level_code(payload: dict) -> str | None:  # type: ignore[type-arg]
    cb = payload.get("_subscribe_callback") or {}
    ci = cb.get("customerInfo") or {}
    code = ci.get("serviceLevel") or cb.get("serviceLevel") or payload.get("serviceLevel")
    return str(code).strip() if code is not None and str(code).strip() else None


def _build_sla_map(db) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """从数据库预加载所有 KSM SLA 等级映射，与内置字典合并。"""
    mapping = dict(_KSM_SLA_FALLBACK)
    try:
        rows = db.execute(
            select(SlaLevel.code, SlaLevel.source_system_code, SlaLevel.name).where(
                or_(SlaLevel.source_system == "KSM", SlaLevel.source_system.is_(None))
            )
        ).all()
        for code, source_code, name in rows:
            if code and name:
                mapping[str(code).strip()] = name
            if source_code and name:
                mapping[str(source_code).strip()] = name
    except Exception as e:
        print(f"Warning: 无法从 sla_levels 表加载映射 ({e})，使用内置字典")
    return mapping


def main(*, dry_run: bool, days: int | None, process_all: bool) -> None:
    init_engine()
    db = next(get_session())
    try:
        sla_map = _build_sla_map(db)
        query = select(Ticket).where(
            Ticket.source_code == "ksm",
            Ticket.deleted_at.is_(None),
            Ticket.source_payload.is_not(None),
        )
        if not process_all and days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            query = query.where(Ticket.received_at >= cutoff)

        tickets = db.execute(query.order_by(Ticket.id.asc())).scalars().all()
        print(f"找到 {len(tickets)} 条 KSM 工单待检查...")

        updated = 0
        skipped = 0
        for ticket in tickets:
            raw_code = _extract_service_level_code(ticket.source_payload or {})
            if not raw_code:
                skipped += 1
                continue

            resolved_name = sla_map.get(raw_code, raw_code)
            if ticket.service_level == resolved_name:
                continue

            old_val = ticket.service_level or "(空)"
            print(f"  {ticket.short_code}: code={raw_code} -> {old_val} => {resolved_name}")
            if not dry_run:
                ticket.service_level = resolved_name
                db.add(ticket)
            updated += 1

        if not dry_run:
            db.commit()
            print(f"\n✅ 已更新 {updated} 条工单 service_level（跳过无数据 {skipped} 条）。")
        else:
            print(f"\n[dry-run] 将更新 {updated} 条工单 service_level（跳过无数据 {skipped} 条），未写入数据库。")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填存量 KSM 工单服务等级")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不写库")
    parser.add_argument("--days", type=int, default=30, help="处理最近 N 天工单（默认 30）")
    parser.add_argument("--all", action="store_true", help="处理全量存量工单（忽略 --days）")
    args = parser.parse_args()

    main(dry_run=args.dry_run, days=None if args.all else args.days, process_all=args.all)

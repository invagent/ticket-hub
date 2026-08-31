"""回填 KSM 工单的退回持久化字段（ksm_accept_opercache_id + ksm_current_node_id）。

对 source_code='ksm'、ksm_accept_opercache_id 为空的工单，若 NoticeStore 里仍有
notice（24h 内推送过），实时重拉 subscribeCallback 详情，提取「受理节点 opercacheId」
（退回目标）+「当前节点 node.id」（退回源）写回。

⚠️ notice 24h TTL 过期后无法回填（拿不到实时详情，source_payload 又是入库快照、
没有受理节点记录）——这些工单只能等 KSM 侧重新推送或人工处理。

用法（在服务器 backend/ 目录下）：
    .venv/bin/python3.12 ../scripts/backfill_ksm_return_fields.py [--dry-run]
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")
from sqlalchemy import select

from adapters.ksm import KSMClient, KSMConfig
from app.config import get_settings
from app.db import get_session, init_engine
from app.models import Ticket
from app.services.ksm.notice_store import NoticeStore
from app.services.ksm.writeback import _return_target_opercache_id


def _node_id(detail: dict) -> str | None:  # type: ignore[type-arg]
    node = detail.get("node") or {}
    nid = node.get("id")
    return nid if isinstance(nid, str) and nid else None


def main(*, dry_run: bool) -> None:
    init_engine()
    settings = get_settings()
    db = next(get_session())
    store = NoticeStore(redis_url=settings.redis_url)
    client = KSMClient(KSMConfig.from_settings(settings))
    try:
        tickets = (
            db.execute(
                select(Ticket).where(
                    Ticket.source_code == "ksm",
                    Ticket.deleted_at.is_(None),
                    Ticket.ksm_accept_opercache_id.is_(None),
                )
            )
            .scalars()
            .all()
        )

        updated = 0
        no_notice = 0
        for ticket in tickets:
            notice = store.get(ticket.source_ticket_id or "")
            if notice is None:
                no_notice += 1
                continue
            try:
                detail = client.get_order_detail(
                    bill_id=ticket.source_ticket_id or "",
                    notice_num=notice.notice_num,
                    subscribe_num=notice.subscribe_num,
                )
            except Exception as e:  # noqa: BLE001 — 单条失败不阻塞其余
                print(f"  {ticket.short_code}: 拉取失败 {e}")
                continue
            accept_oc = _return_target_opercache_id(detail)
            node_id = _node_id(detail)
            if not accept_oc:
                print(f"  {ticket.short_code}: 详情无受理节点，跳过")
                continue
            print(f"  {ticket.short_code}: accept={accept_oc} node={node_id}")
            if not dry_run:
                ticket.ksm_accept_opercache_id = accept_oc
                ticket.ksm_current_node_id = node_id
                db.add(ticket)
            updated += 1

        if not dry_run:
            db.commit()
        print(f"\n{'[dry-run] ' if dry_run else ''}回填 {updated} 条，notice 过期 {no_notice} 条。")
    finally:
        client.close()
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)

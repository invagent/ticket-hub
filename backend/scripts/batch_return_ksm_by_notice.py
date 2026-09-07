"""一次性运维脚本：批量把「已找到 noticeNum」的历史 KSM 工单重新拉取详情并退回。

背景：一批老 KSM 工单（webhook 早已推过、notice 已过期）用户从 KSM 侧手工
翻出了它们各自最新一次流转对应的 noticeNum（subscribeNum 固定是
"ksm_feedback_change"，见 KSM 接口约定）。有了新鲜 notice 才能实时拉取详情——
退回（returnKsmOrder）依赖「最新节点的上一个节点」算法，必须基于刚拉取的
新鲜数据（见 services/ksm/writeback.py `_refresh_for_return` 的说明），不能
用旧快照。

流程（每条 bill_id）：
    1. NoticeStore.put(bill_id, notice) —— 存最新凭证，24h TTL
    2. KSMClient.get_order_detail 实时验证凭证有效（拉不到直接跳过，不猜）
    3. request_return(deal_opinion=...) —— 入 return outbox
    4. 全部入队后统一 drain_ksm_outbox 一次性消费（真正调用 KSM returnKsmOrder）

用法（SIT 容器内，backend/ 目录下）：
    python3 scripts/batch_return_ksm_by_notice.py [--dry-run]

--dry-run 只做步骤 1-2（存凭证+验证拉取），不入队不退回。
"""

from __future__ import annotations

import argparse

from app.config import get_settings
from app.db import make_session
from app.models import Ticket
from app.services.cascade.return_sync import ReturnSyncError, request_return
from app.services.ksm.notice_store import NoticeInfo, NoticeStore
from app.services.ksm.writeback import drain_ksm_outbox

_SUBSCRIBE_NUM = "ksm_feedback_change"
_DEAL_OPINION = "历史工单补充凭证后退回重新分派"
_CHANGED_BY = "user:刘伟成"

# (billNumber, billId, noticeNum) —— 用户提供，billNumber 仅供日志辨认。
_ROWS: list[tuple[str, str, str]] = [
    ("R20260824-3159", "59C8CA59C902580CE0639BCAA8C0D7EA", "2553195262139663360"),
    ("R20260824-3334", "59C92E8D4E64C828E0639BCAA8C0CF1D", "2553209168371351552"),
    ("R20260824-3362", "59C94A7B89E9991BE0639BCAA8C0F985", "2553213286699729920"),
    ("R20260824-3408", "59C98215CD84F8B1E0639BCAA8C04F90", "2553220294534474752"),
    ("R20260824-3562", "59CC3D89B9256285E0639BCAA8C04D20", "2553318733281752064"),
    ("R20260825-2978", "59DC7E34557EC3D1E0639BCAA8C0D2DB", "2553905255156544512"),
    ("R20260826-3046", "59F0D48D80D81A53E0639BCAA8C0794A", "2554637056211648512"),
    ("R20260826-3330", "59F145F44823796EE0639BCAA8C082CD", "2554652990196383744"),
    ("R20260826-3351", "59F14A1DFA2EB82FE0639BCAA8C0F7C2", "2554654097408764928"),
    ("R20260826-3675", "59F4867071AD4844E0639BCAA8C03CD1", "2554770155738213376"),
    ("R20260827-3696", "5A064E26001BC8C8E0639BCAA8C06B52", "2555410741957513216"),
    ("R20260827-3762", "5A072846139E5A65E0639BCAA8C0E78D", "2555441450763964416"),
    ("R20260827-3857", "5A09DAFC11A0DDA2E0639BCAA8C02011", "2555538663908551680"),
    ("R20260828-3088", "5A19E8375747E386E0639BCAA8C0D10F", "2556116972480224256"),
    ("R20260831-0003", "5A47E79C246B3E57E0639BCAA8C0BA84", "2557774228872723456"),
    ("R20260831-1267", "5A50BF82FB5F97F2E0639BCAA8C00A3B", "2558095816419687424"),
    ("R20260831-2081", "5A53C3701C5F8EEBE0639BCAA8C0F46F", "2558201562977264640"),
    ("R20260831-2307", "5A544017AEC2E084E0639BCAA8C0B1E5", "2558219156286059520"),
    ("R20260831-2973", "5A556BAA9429F514E0639BCAA8C09335", "2558261173112007680"),
    ("R20260831-3005", "5A557BA86C2D73A1E0639BCAA8C09377", "2558263423164726272"),
    ("R20260831-3367", "5A566B0D9FD92370E0639BCAA8C0BBB2", "2558297113087962112"),
]


def main(*, dry_run: bool) -> None:
    settings = get_settings()
    store = NoticeStore(redis_url=settings.redis_url)
    db = make_session()
    ok_ticket_ids: list[int] = []
    try:
        from adapters.ksm import KSMClient, KSMConfig

        client = KSMClient(KSMConfig.from_settings(settings))

        for bill_number, bill_id, notice_num in _ROWS:
            store.put(bill_id, NoticeInfo(notice_num=notice_num, subscribe_num=_SUBSCRIBE_NUM))
            try:
                detail = client.get_order_detail(
                    bill_id=bill_id, notice_num=notice_num, subscribe_num=_SUBSCRIBE_NUM
                )
            except Exception as e:  # noqa: BLE001 — 汇总报告，单条失败不中止整批
                print(f"[SKIP] {bill_number} ({bill_id}) 实时拉取失败: {e}")
                continue

            node = (detail or {}).get("node") or {}
            print(f"[OK]   {bill_number} ({bill_id}) 拉取成功，当前节点: {node.get('name')}")

            ticket = db.query(Ticket).filter_by(source_ticket_id=bill_id).first()
            if ticket is None:
                print(f"       -> 系统内无此 ticket，跳过退回")
                continue
            print(f"       -> {ticket.short_code} status={ticket.status} takeover={ticket.ksm_takeover_status}")

            if dry_run:
                continue

            try:
                result = request_return(
                    db, ticket.id, deal_opinion=_DEAL_OPINION, requested_by=_CHANGED_BY
                )
                print(f"       -> 已入队 outbox_id={result.outbox_id}")
                ok_ticket_ids.append(ticket.id)
            except ReturnSyncError as e:
                print(f"       -> 入队失败: {e}")

        if dry_run:
            print("\n[dry-run] 仅完成凭证存储+实时验证，未入队退回。")
            return

        print(f"\n共 {len(ok_ticket_ids)} 条已入队，开始 drain...")
        drain_result = drain_ksm_outbox(db, client=client, notice_store=store, settings=settings)
        print(f"drain 结果: {drain_result}")
    finally:
        db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    main(dry_run=args.dry_run)

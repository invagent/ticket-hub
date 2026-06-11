"""Build routing replay fixture from the recorded historical tickets.

Reads `backend/tests/fixtures/recorded/historical_tickets.json` (50 真实
KSM 工单的 xlsx 导出) and emits `backend/tests/eval/routing_v1.jsonl` —
the (product_line, module, expected_user_ids) labels per ticket are kept
in this file as `LABELS`. Re-run after adding new historical samples.

Label sources:
  - product_name → product_line_code (4 个金蝶产品)
  - title 关键词 + 人工复核 → module + 期望 owner

Module owners (employee_no → user_id used in the in-memory replay DB):
  数电开票    K0030 → 30
  收票采集    K0031 → 31
  费用报销    K0032 → 32
  全票池同步  K0033 → 33
  系统配置    K0034 → 34
  接口集成    K0035 → 35
  兜底池      K0099 → 99 (default_pool)

Hit-rate gate stays at ≥90% (D1 验收门槛)。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
SOURCE = BACKEND_DIR / "tests" / "fixtures" / "recorded" / "historical_tickets.json"
TARGET = BACKEND_DIR / "tests" / "eval" / "routing_v1.jsonl"

PRODUCT_MAP = {
    "金蝶发票云": "cloud-fapiao",
    "金蝶云星空": "cloud-erp-star",
    "金蝶云苍穹": "cloud-cangqiong",
    "金蝶EAS Cloud": "eas-cloud",
}

# Per-ticket labels: 1-based index → (module|None, owner_user_id|None, decision)
# 索引对应 historical_tickets.json 中 records 的顺序（与 xlsx 行序一致）
# decision='assigned' / 'default_pool'。default_pool 时 module=None, owner=99。
LABELS: dict[int, tuple[str | None, int, str]] = {
    1:  ("系统配置",   34, "assigned"),
    2:  ("费用报销",   32, "assigned"),
    3:  ("数电开票",   30, "assigned"),
    4:  ("费用报销",   32, "assigned"),
    5:  ("数电开票",   30, "assigned"),
    6:  ("数电开票",   30, "assigned"),
    7:  ("全票池同步", 33, "assigned"),
    8:  ("接口集成",   35, "assigned"),
    9:  ("数电开票",   30, "assigned"),
    10: ("数电开票",   30, "assigned"),
    11: ("全票池同步", 33, "assigned"),
    12: ("费用报销",   32, "assigned"),
    13: ("费用报销",   32, "assigned"),
    14: ("收票采集",   31, "assigned"),
    15: ("系统配置",   34, "assigned"),
    16: ("系统配置",   34, "assigned"),
    17: ("费用报销",   32, "assigned"),
    18: ("系统配置",   34, "assigned"),
    19: ("系统配置",   34, "assigned"),
    20: ("收票采集",   31, "assigned"),
    21: ("收票采集",   31, "assigned"),
    22: ("接口集成",   35, "assigned"),
    23: ("接口集成",   35, "assigned"),
    24: ("数电开票",   30, "assigned"),
    25: ("数电开票",   30, "assigned"),
    26: ("费用报销",   32, "assigned"),
    27: ("全票池同步", 33, "assigned"),
    28: ("费用报销",   32, "assigned"),
    29: ("全票池同步", 33, "assigned"),
    30: ("系统配置",   34, "assigned"),
    31: ("系统配置",   34, "assigned"),
    32: ("收票采集",   31, "assigned"),
    33: ("接口集成",   35, "assigned"),
    34: ("接口集成",   35, "assigned"),
    35: ("接口集成",   35, "assigned"),
    36: (None,         99, "default_pool"),  # 标题过短无法分类
    37: (None,         99, "default_pool"),  # 标题过短无法分类
    38: ("系统配置",   34, "assigned"),
    39: ("全票池同步", 33, "assigned"),
    40: ("数电开票",   30, "assigned"),
    41: ("全票池同步", 33, "assigned"),
    42: ("数电开票",   30, "assigned"),
    43: ("收票采集",   31, "assigned"),
    44: ("数电开票",   30, "assigned"),
    45: ("数电开票",   30, "assigned"),
    46: ("收票采集",   31, "assigned"),
    47: ("全票池同步", 33, "assigned"),
    48: ("接口集成",   35, "assigned"),
    49: ("数电开票",   30, "assigned"),
    50: ("接口集成",   35, "assigned"),
}


def build() -> list[dict]:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    records = payload["records"]
    assert len(records) == 50, f"expected 50 records, got {len(records)}"

    out: list[dict] = []
    for idx, rec in enumerate(records, start=1):
        module, owner_uid, decision = LABELS[idx]
        product_code = PRODUCT_MAP.get(rec.get("product_name") or "", None)
        scope = "default_pool" if decision == "default_pool" else "module"
        out.append(
            {
                "id": f"hist-{idx:03d}",
                "source_ticket_id": rec["ksm_ticket_id"],
                "title": rec["title"],
                "product_line_code": product_code,
                "module": module,
                "feature": None,
                "expected_decision": decision,
                "expected_user_ids": [owner_uid],
                "expected_scope": scope,
            }
        )
    return out


def main() -> None:
    fixture = build()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", encoding="utf-8") as f:
        for r in fixture:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # quick stats
    from collections import Counter
    by_mod = Counter(r["module"] or "default_pool" for r in fixture)
    by_pl = Counter(r["product_line_code"] or "null" for r in fixture)
    print(f"wrote {TARGET} ({len(fixture)} records)")
    print("by module:", dict(by_mod))
    print("by product_line:", dict(by_pl))


if __name__ == "__main__":
    main()

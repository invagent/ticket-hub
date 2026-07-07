"""开发用：往飞书「测试知识库」塞几篇 FAQ/知识 docs（ADR-0016 P3 KB 闭环联调）.

覆盖三种病因诊断场景，配合 seed_escalations.py 造的 escalation 工单：
  - 开票超时FAQ（正解=通道拥堵）→ escalation 里 AI 答成「重新实名」= skill 没用好
  - 数电额度说明（故意写过时限额）→ AI 照旧答 = knowledge 过时
  - 红冲操作指引（正常条目）
  - （故意不建「批量导出」文档）→ 客户问导出无命中 = retrieval 缺失

幂等：按标题跳过已存在节点。只读安全——只新增，不改不删既有内容。

Usage（backend venv，需 FEISHU_* + FEISHU_WIKI_SPACE_ID 配好）：
    backend/.venv/bin/python scripts/seed/seed_feishu_kb.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

FEISHU_BASE = "https://open.feishu.cn"

# 每篇：标题 + 段落列表（段落写成 docx text block）
DOCS: list[tuple[str, list[str]]] = [
    (
        "【测试】开票超时排查FAQ",
        [
            "问题：开票时提示「认证超时」，实名认证已完成仍失败。",
            "正解：此现象绝大多数是税局通道拥堵所致，不是账号或实名问题。"
            "无需重新实名认证。请避开 9:00-11:00 高峰时段重试；若持续，"
            "提交工单并附 bill_id，由后台核查通道状态。",
            "关键判别：若客户明确表示「实名已完成」，则不应再引导其去实名认证。",
        ],
    ),
    (
        "【测试】数电发票开票额度说明",
        [
            "问题：数电发票开票时提示「超出可用额度」。",
            "说明：每个纳税主体有月度开票总额上限，超出后当月无法继续开具，"
            "需次月自动恢复，或向税局申请临时额度调增。",
            "当前默认上限：500 万元/月。（注：此为示例知识库中的旧数值，"
            "用于演示「知识过时」诊断——真实额度以税局最新核定为准。）",
        ],
    ),
    (
        "【测试】发票红冲操作指引",
        [
            "问题：如何对已开具的数电发票做红冲（红字冲销）？",
            "步骤：① 在开票平台找到原蓝票 → ② 发起「红字确认单」→ "
            "③ 购销双方确认 → ④ 依确认单开具红字发票完成冲销。",
            "注意：红冲完成后，关联业务单据的开票状态需等待同步，"
            "通常数分钟内刷新；若长时间未更新，属状态同步异常，需提工单排查。",
        ],
    ),
]


def _tat(app_id: str, app_secret: str) -> str:
    r = httpx.post(
        f"{FEISHU_BASE}/open-apis/auth/v3/app_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=15,
    )
    return str(r.json()["tenant_access_token"])


def _write_blocks(tat: str, doc_id: str, paragraphs: list[str]) -> None:
    body = {
        "children": [
            {"block_type": 2, "text": {"elements": [{"text_run": {"content": p}}]}}
            for p in paragraphs
        ]
    }
    r = httpx.post(
        f"{FEISHU_BASE}/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
        headers={"Authorization": f"Bearer {tat}", "Content-Type": "application/json"},
        json=body,
        timeout=20,
    )
    b = r.json()
    if b.get("code") != 0:
        raise SystemExit(f"write blocks failed for {doc_id}: {b.get('code')} {b.get('msg')}")


def main() -> int:
    from adapters.feishu import FeishuClient, FeishuConfig
    from app.config import get_settings

    s = get_settings()
    if not (s.feishu_app_id and s.feishu_wiki_space_id):
        print("ERROR: FEISHU_APP_ID / FEISHU_WIKI_SPACE_ID 未配置", file=sys.stderr)
        return 2

    space = s.feishu_wiki_space_id
    client = FeishuClient(FeishuConfig.from_settings(s))
    tat = _tat(s.feishu_app_id, s.feishu_app_secret)
    try:
        existing = {n.title for n in client.walk_wiki_tree(space)}
        print(f"空间现有节点标题：{sorted(existing)}")
        for title, paras in DOCS:
            if title in existing:
                print(f"  · 跳过（已存在）：{title}")
                continue
            node = client.create_wiki_node(space, title=title, parent_node_token=None)
            _write_blocks(tat, node.obj_token, paras)
            print(f"  ✅ 新建：{title}  node={node.node_token}")
    finally:
        client.close()
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

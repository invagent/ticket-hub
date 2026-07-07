"""开发用：造 AI 客服 escalation 工单（ADR-0016 P3 反思闭环联调）.

4 个 ai_cs 工单，黄金三元组 + conversation + cited_knowledge，配合
seed_feishu_kb.py 的 KB 文档，覆盖三种病因诊断：
  esc-skill-001     引用了对的 FAQ 但 AI 答成「重新实名」→ skill 没用好
  esc-knowledge-002 引用的额度知识过时（500万旧值）→ knowledge 过时
  esc-retrieval-003 问「批量导出」KB 无条目、cited 为空 → retrieval 缺失
  esc-mixed-004     红冲状态 + 超时双问题、AI 答非所问 → knowledge+skill 混合

predicted_type='Operation' 让它们进反思队列（Operation-only 过滤）。
幂等：按 session_id 跳过已存在。

Usage（backend venv）：
    backend/.venv/bin/python scripts/seed/seed_escalations.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

CASES = [
    {
        "session_id": "dev-esc-skill-001",
        "question": "开票一直提示认证超时，我实名早就做完了，怎么还失败？",
        "ai_answer": "请您先完成实名认证后再尝试开票。",
        "dissatisfaction": "我都说了实名早做完了，你还让我认证，根本没解决问题。",
        "conversation": [
            {"role": "user", "text": "开票提示认证超时"},
            {"role": "assistant", "text": "请先完成实名认证。"},
            {"role": "user", "text": "实名早做完了！还是超时"},
        ],
        "cited_knowledge": [
            {
                "type": "wiki",
                "title": "【测试】开票超时排查FAQ",
                "snippet": "此现象绝大多数是税局通道拥堵所致，不是账号或实名问题。无需重新实名认证。",
                "score": 0.74,
            }
        ],
    },
    {
        "session_id": "dev-esc-knowledge-002",
        "question": "数电票开票提示超出额度，我这个月才开了 300 万啊，怎么会超？",
        "ai_answer": "每月开票额度上限为 500 万元，您可能已达上限，请次月再开或申请调增。",
        "dissatisfaction": "额度早就调过了，你们的信息是老黄历，误导人。",
        "conversation": [
            {"role": "user", "text": "开票提示超额度，才开300万"},
            {"role": "assistant", "text": "每月上限500万，请次月再开。"},
            {"role": "user", "text": "额度早调了，你信息是旧的"},
        ],
        "cited_knowledge": [
            {
                "type": "wiki",
                "title": "【测试】数电发票开票额度说明",
                "snippet": "当前默认上限：500 万元/月。",
                "score": 0.82,
            }
        ],
    },
    {
        "session_id": "dev-esc-retrieval-003",
        "question": "发票能不能批量导出成 Excel 表格？",
        "ai_answer": "抱歉，暂不支持发票批量导出功能。",
        "dissatisfaction": "明明发票池就有导出按钮，你根本不了解产品。",
        "conversation": [
            {"role": "user", "text": "发票能批量导出Excel吗"},
            {"role": "assistant", "text": "暂不支持批量导出。"},
            {"role": "user", "text": "发票池明明有导出按钮"},
        ],
        "cited_knowledge": [],
    },
    {
        "session_id": "dev-esc-mixed-004",
        "question": "红冲之后开票状态一直没更新，而且还老提示认证超时，到底怎么回事？",
        "ai_answer": "请重新实名认证，并等待红冲审核完成。",
        "dissatisfaction": "答非所问，两个问题都没说清楚。",
        "conversation": [
            {"role": "user", "text": "红冲后状态没更新，还提示超时"},
            {"role": "assistant", "text": "请重新实名认证并等待红冲审核。"},
            {"role": "user", "text": "答非所问"},
        ],
        "cited_knowledge": [
            {
                "type": "wiki",
                "title": "【测试】发票红冲操作指引",
                "snippet": "红冲完成后，关联业务单据的开票状态需等待同步。",
                "score": 0.61,
            },
            {
                "type": "wiki",
                "title": "【测试】开票超时排查FAQ",
                "snippet": "此现象绝大多数是税局通道拥堵所致。",
                "score": 0.58,
            },
        ],
    },
]


def main() -> int:
    from app.db import make_session
    from app.models import Source, Ticket
    from app.repositories.ticket import TicketRepository

    with make_session() as db:
        if db.query(Source).filter_by(code="ai_cs").first() is None:
            db.add(Source(code="ai_cs", name="AI客服"))
            db.commit()
        repo = TicketRepository(db)
        created = 0
        for c in CASES:
            if repo.find_by_source("ai_cs", c["session_id"]) is not None:
                print(f"  · 跳过（已存在）：{c['session_id']}")
                continue
            ai_cs = {
                "original_question": c["question"],
                "ai_answer": c["ai_answer"],
                "dissatisfaction": c["dissatisfaction"],
                "conversation": c["conversation"],
                "cited_knowledge": c["cited_knowledge"],
                "skills_used": ["customer-service"],
            }
            t = Ticket(
                short_code=repo.next_short_code(),
                source_code="ai_cs",
                source_ticket_id=c["session_id"],
                type="Raw",
                status="received",
                source_payload={"ai_cs": ai_cs},
                title=c["question"][:120],
                body=c["question"],
                predicted_type="Operation",
                predicted_confidence=0.6,
                classified_at=datetime.now(UTC),
                reporter={"name": "测试客户"},
            )
            db.add(t)
            db.commit()
            created += 1
            print(f"  ✅ 新建 escalation：{c['session_id']}  {t.short_code}")
        print(f"done. 新建 {created} 个。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

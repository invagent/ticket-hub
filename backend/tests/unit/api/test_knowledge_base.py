"""Unit tests for Knowledge Base API and ID generator."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.auth import issue_jwt
from app.models import KnowledgeBaseItem, Ticket, User
from app.repositories.knowledge_base import generate_knowledge_id


def _bearer(uid: int = 1, *, name: str = "alice", role: str = "supervisor") -> dict[str, str]:
    token, _ = issue_jwt(sub=str(uid), name=name, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def kb_world(db_session: Session) -> Session:
    u1 = User(id=1, feishu_uid="ou_1", name="alice", role="supervisor")
    u2 = User(id=2, feishu_uid="ou_2", name="张工", role="assignee")
    t = Ticket(
        id=10,
        short_code="TKT-KB-10",
        source_code="ksm",
        source_ticket_id="ksm-kb-10",
        type="Raw",
        status="processing",
        title="测试知识库工单",
        handler_user_id=2,  # 处理人为张工
    )
    db_session.add_all([u1, u2, t])
    db_session.commit()
    yield db_session


def test_generate_knowledge_id_daily_sequence(kb_world: Session) -> None:
    dt = datetime(2026, 9, 10, 10, 0, tzinfo=UTC)
    id1 = generate_knowledge_id(kb_world, dt)
    assert id1 == "FPYFAQ202609100001"

    # 模拟已有一条
    kb_world.add(
        KnowledgeBaseItem(
            id=id1,
            title="标题1",
            content="内容1",
            type="FAQ",
            product_line_code="pl-1",
            product_line_name="产品线1",
            module_code="m-1",
            module_name="模块1",
            status="pending_review",
            created_by="张三",
        )
    )
    kb_world.commit()

    id2 = generate_knowledge_id(kb_world, dt)
    assert id2 == "FPYFAQ202609100002"


def test_create_knowledge_item_defaults_to_pending_review(
    app_client: TestClient, kb_world: Session
) -> None:
    payload = {
        "title": "数电票登录白屏排查指南",
        "content": "先检查端口是否被占用，执行 netstat -ano...",
        "type": "FAQ",
        "product_line_code": "invoice_cloud",
        "product_line_name": "数电发票云",
        "module_code": "auth",
        "module_name": "登录认证",
    }
    r = app_client.post("/api/knowledge-base", json=payload, headers=_bearer(1))
    assert r.status_code == 201
    data = r.json()
    assert data["id"].startswith("FPYFAQ")
    assert data["status"] == "pending_review"  # 默认状态为待审核
    assert data["created_by"] == "alice"
    assert data["total_calls"] == 0


def test_create_knowledge_item_binds_ticket_handler(
    app_client: TestClient, kb_world: Session
) -> None:
    """从工单发起提交知识：创建人自动绑定为该工单的处理人。"""
    payload = {
        "title": "进项税发票勾选超时解决方案",
        "content": "建议按 200 张分批次认证...",
        "type": "操作手册",
        "product_line_code": "invoice_cloud",
        "product_line_name": "数电发票云",
        "module_code": "deduct",
        "module_name": "进项抵扣",
        "ticket_id": 10,  # 工单 10 处理人为张工 (User 2)
    }
    r = app_client.post("/api/knowledge-base", json=payload, headers=_bearer(1, name="alice"))
    assert r.status_code == 201
    data = r.json()
    assert data["created_by"] == "张工"  # 自动解析绑定工单处理人
    assert data["ticket_id"] == 10
    assert data["status"] == "pending_review"


def test_list_knowledge_items_filtering(app_client: TestClient, kb_world: Session) -> None:
    kb_world.add_all(
        [
            KnowledgeBaseItem(
                id="FPYFAQ202609100010",
                title="税控盘离线常见问题",
                content="请检查金税盘服务状态",
                type="FAQ",
                product_line_code="tax_disk",
                product_line_name="税控盘系统",
                module_code="core",
                module_name="核心驱动",
                status="pending_review",
                created_by="李四",
            ),
            KnowledgeBaseItem(
                id="FPYFAQ202609100011",
                title="数电票打印插件配置",
                content="安装打印驱动并绑定 9100 端口",
                type="交付配置",
                product_line_code="invoice_cloud",
                product_line_name="数电发票云",
                module_code="print",
                module_name="发票打印",
                status="active",
                created_by="王五",
            ),
        ]
    )
    kb_world.commit()

    # 筛选状态 pending_review
    r1 = app_client.get("/api/knowledge-base?status=pending_review", headers=_bearer())
    assert r1.status_code == 200
    ids1 = [it["id"] for it in r1.json()["items"]]
    assert "FPYFAQ202609100010" in ids1
    assert "FPYFAQ202609100011" not in ids1

    # 筛选搜索关键字
    r2 = app_client.get("/api/knowledge-base?search=打印驱动", headers=_bearer())
    assert r2.status_code == 200
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["id"] == "FPYFAQ202609100011"


def test_batch_review_knowledge(app_client: TestClient, kb_world: Session) -> None:
    item_id = "FPYFAQ202609100020"
    kb_world.add(
        KnowledgeBaseItem(
            id=item_id,
            title="红字发票冲红说明",
            content="需确认原发票状态未冲红",
            type="FAQ",
            product_line_code="inv",
            product_line_name="发票",
            module_code="red",
            module_name="红字",
            status="pending_review",
            created_by="赵六",
        )
    )
    kb_world.commit()

    # 审核通过
    r = app_client.post(
        "/api/knowledge-base/batch-review",
        json={"ids": [item_id], "action": "approve"},
        headers=_bearer(1, name="王主管"),
    )
    assert r.status_code == 200
    assert r.json()["reviewed_count"] == 1

    item = kb_world.get(KnowledgeBaseItem, item_id)
    assert item is not None
    assert item.status == "active"
    assert item.reviewed_by == "王主管"
    assert item.reviewed_at is not None


def test_batch_offline_and_online(app_client: TestClient, kb_world: Session) -> None:
    item_id = "FPYFAQ202609100030"
    kb_world.add(
        KnowledgeBaseItem(
            id=item_id,
            title="测试知识下架与上架",
            content="说明内容",
            type="FAQ",
            product_line_code="inv",
            product_line_name="发票",
            module_code="m",
            module_name="模",
            status="active",
            created_by="孙七",
        )
    )
    kb_world.commit()

    # 批量下架
    r1 = app_client.post(
        "/api/knowledge-base/batch-offline",
        json={"ids": [item_id]},
        headers=_bearer(),
    )
    assert r1.status_code == 200
    assert r1.json()["offline_count"] == 1
    assert kb_world.get(KnowledgeBaseItem, item_id).status == "offline"

    # 批量上架
    r2 = app_client.post(
        "/api/knowledge-base/batch-online",
        json={"ids": [item_id]},
        headers=_bearer(),
    )
    assert r2.status_code == 200
    assert r2.json()["online_count"] == 1
    assert kb_world.get(KnowledgeBaseItem, item_id).status == "active"

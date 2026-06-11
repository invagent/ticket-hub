"""Router unit tests against in-memory SQLite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models import (
    AssignmentScopeFeature,
    AssignmentScopeModule,
    ProductLine,
    User,
    UserPartner,
)
from app.services.routing.router import Router, RouteRequest


@pytest.fixture
def routing_world(db_session: Session) -> Iterator[Session]:
    """Seed: 4 users, 1 partner pair (alice<->bob), 1 product_line."""
    db_session.add(ProductLine(code="cloud-erp", name="Cloud ERP"))
    db_session.add_all(
        [
            User(id=1, feishu_uid="ou_alice", name="alice", role="assignee"),
            User(id=2, feishu_uid="ou_bob", name="bob", role="assignee"),
            User(id=3, feishu_uid="ou_carol", name="carol", role="assignee"),
            User(id=4, feishu_uid="ou_dave", name="dave", role="assignee"),
            User(id=99, feishu_uid="ou_pool", name="default-pool", role="assignee"),
        ]
    )
    db_session.flush()
    # alice <-> bob symmetric partnership
    db_session.add_all(
        [
            UserPartner(user_id=1, partner_id=2),
            UserPartner(user_id=2, partner_id=1),
        ]
    )
    db_session.commit()
    yield db_session


def _req(**kw) -> RouteRequest:  # type: ignore[no-untyped-def]
    defaults = {
        "ticket_id": 100,
        "source_code": "ksm",
        "product_line_code": "cloud-erp",
    }
    defaults.update(kw)
    return RouteRequest(**defaults)


# ---- module path -----------------------------------------------------------


def test_module_unique_user_assigned(routing_world: Session) -> None:
    routing_world.add(
        AssignmentScopeModule(user_id=3, product_line_code="cloud-erp", module="应付管理")
    )
    routing_world.commit()

    decision = Router(routing_world).route(_req(raw_module="应付管理"))
    assert decision.decision == "assigned"
    assert decision.matched_scope == "module"
    assert decision.assigned_user_ids == [3]
    assert decision.confidence >= 0.9


def test_module_partner_group_collapses_to_single_assigned(
    routing_world: Session,
) -> None:
    """alice and bob own the same module — partner group, NOT multi_match."""
    routing_world.add_all(
        [
            AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理"),
            AssignmentScopeModule(user_id=2, product_line_code="cloud-erp", module="应付管理"),
        ]
    )
    routing_world.commit()

    decision = Router(routing_world).route(_req(raw_module="应付管理"))
    assert decision.decision == "assigned"
    assert decision.assigned_user_ids == [1, 2]


def test_module_multi_group_returns_multi_match(routing_world: Session) -> None:
    """alice/bob (group 1) + carol (alone, group 2) own same module → multi_match."""
    routing_world.add_all(
        [
            AssignmentScopeModule(user_id=1, product_line_code="cloud-erp", module="应付管理"),
            AssignmentScopeModule(user_id=2, product_line_code="cloud-erp", module="应付管理"),
            AssignmentScopeModule(user_id=3, product_line_code="cloud-erp", module="应付管理"),
        ]
    )
    routing_world.commit()

    decision = Router(routing_world).route(_req(raw_module="应付管理"))
    assert decision.decision == "multi_match"
    assert decision.matched_scope == "module"
    assert set(decision.assigned_user_ids) == {1, 2, 3}


def test_module_misses_falls_through_to_feature(routing_world: Session) -> None:
    routing_world.add(AssignmentScopeFeature(user_id=4, feature="批量导出"))
    routing_world.commit()

    decision = Router(routing_world).route(_req(raw_module="不存在的模块", raw_feature="批量导出"))
    assert decision.decision == "assigned"
    assert decision.matched_scope == "feature"
    assert decision.assigned_user_ids == [4]


# ---- feature path ---------------------------------------------------------


def test_feature_unique_user(routing_world: Session) -> None:
    routing_world.add(AssignmentScopeFeature(user_id=4, feature="批量导出"))
    routing_world.commit()
    decision = Router(routing_world).route(_req(raw_feature="批量导出"))
    assert decision.decision == "assigned"
    assert decision.matched_scope == "feature"
    assert decision.assigned_user_ids == [4]


def test_feature_partner_group(routing_world: Session) -> None:
    routing_world.add_all(
        [
            AssignmentScopeFeature(user_id=1, feature="数据导入"),
            AssignmentScopeFeature(user_id=2, feature="数据导入"),
        ]
    )
    routing_world.commit()
    decision = Router(routing_world).route(_req(raw_feature="数据导入"))
    assert decision.decision == "assigned"
    assert decision.matched_scope == "feature"
    assert decision.assigned_user_ids == [1, 2]


def test_feature_multi_group(routing_world: Session) -> None:
    routing_world.add_all(
        [
            AssignmentScopeFeature(user_id=1, feature="批量"),
            AssignmentScopeFeature(user_id=2, feature="批量"),
            AssignmentScopeFeature(user_id=3, feature="批量"),
        ]
    )
    routing_world.commit()
    decision = Router(routing_world).route(_req(raw_feature="批量"))
    assert decision.decision == "multi_match"
    assert decision.matched_scope == "feature"
    assert set(decision.assigned_user_ids) == {1, 2, 3}


# ---- default_pool / no-hit ------------------------------------------------


def test_no_hit_with_default_pool_returns_default_pool(routing_world: Session) -> None:
    decision = Router(routing_world, default_pool_user_id=99).route(
        _req(raw_module="未配置", raw_feature="也没配置")
    )
    assert decision.decision == "default_pool"
    assert decision.assigned_user_ids == [99]


def test_no_hit_without_default_pool_returns_empty(routing_world: Session) -> None:
    decision = Router(routing_world).route(_req(raw_module="未配置"))
    assert decision.decision == "default_pool"
    assert decision.assigned_user_ids == []


def test_module_priority_over_feature(routing_world: Session) -> None:
    """If module matches, feature is NOT consulted (even if richer)."""
    routing_world.add(AssignmentScopeModule(user_id=3, product_line_code="cloud-erp", module="M"))
    routing_world.add_all(
        [
            AssignmentScopeFeature(user_id=1, feature="F"),
            AssignmentScopeFeature(user_id=2, feature="F"),
            AssignmentScopeFeature(user_id=4, feature="F"),
        ]
    )
    routing_world.commit()

    decision = Router(routing_world).route(_req(raw_module="M", raw_feature="F"))
    assert decision.matched_scope == "module"
    assert decision.assigned_user_ids == [3]


# ---- Edge cases ------------------------------------------------------------


def test_no_product_line_skips_module_to_feature(routing_world: Session) -> None:
    routing_world.add(AssignmentScopeModule(user_id=3, product_line_code="cloud-erp", module="M"))
    routing_world.add(AssignmentScopeFeature(user_id=4, feature="F"))
    routing_world.commit()

    decision = Router(routing_world).route(
        RouteRequest(
            ticket_id=1,
            source_code="ksm",
            product_line_code=None,
            raw_module="M",
            raw_feature="F",
        )
    )
    # Without product_line, module step is skipped → feature wins
    assert decision.matched_scope == "feature"
    assert decision.assigned_user_ids == [4]


def test_module_match_other_product_line_does_not_leak(routing_world: Session) -> None:
    """Module 'M' belongs to product_line 'other'; querying cloud-erp shouldn't hit."""
    routing_world.add(ProductLine(code="other", name="Other"))
    routing_world.commit()
    routing_world.add(AssignmentScopeModule(user_id=3, product_line_code="other", module="M"))
    routing_world.commit()

    decision = Router(routing_world, default_pool_user_id=99).route(_req(raw_module="M"))
    # module hit nothing in cloud-erp; no feature → default_pool
    assert decision.decision == "default_pool"
    assert decision.assigned_user_ids == [99]

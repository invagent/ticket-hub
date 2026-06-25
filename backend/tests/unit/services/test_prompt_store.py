"""prompt_store 测试 — DB 覆盖/文件兜底/编辑/回滚/导入。"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models import SkillPrompt
from app.services.skills import prompt_store as ps


@pytest.fixture(autouse=True)
def _clear_cache():
    ps.clear_cache()
    yield
    ps.clear_cache()


def test_load_falls_back_to_file_when_no_db_row(monkeypatch: pytest.MonkeyPatch) -> None:
    # 真实文件 classify_v2.md 存在 → 文件兜底（DB 在单测里读不到表）
    content = ps.load_prompt("classify_v2")
    assert "分类" in content or "Operation" in content


def test_load_missing_raises() -> None:
    with pytest.raises(ps.PromptNotFoundError):
        ps.load_prompt("does_not_exist_xyz")


def test_db_override_beats_file(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    # 让 make_session() 返回测试 session，模拟 DB 有行覆盖文件
    monkeypatch.setattr(ps, "make_session", lambda: _CtxSession(db_session))
    db_session.add(SkillPrompt(name="classify_v2", type="llm", content_md="DB 覆盖内容", version=3))
    db_session.commit()
    assert ps.load_prompt("classify_v2") == "DB 覆盖内容"


class _CtxSession:
    """包装已存在的 session 成上下文管理器（make_session 用法是 with ... as db）。"""

    def __init__(self, s: Session) -> None:
        self._s = s

    def __enter__(self) -> Session:
        return self._s

    def __exit__(self, *_: object) -> None:
        pass


def test_import_from_files_idempotent(db_session: Session) -> None:
    n1 = ps.import_prompts_from_files(db_session)
    assert n1 >= 5  # classify_v1/v2, dedup, conflict, escalation, vision...
    n2 = ps.import_prompts_from_files(db_session)
    assert n2 == 0  # 已存在跳过
    assert ps.get_prompt_row(db_session, "classify_v2") is not None


def test_edit_bumps_version_and_history(db_session: Session) -> None:
    ps.import_prompts_from_files(db_session)
    v = ps.edit_prompt(
        db_session, "dedup_v1", "新去重提示词", operator="user:boss", reason="调阈值"
    )
    assert v == 2
    row = ps.get_prompt_row(db_session, "dedup_v1")
    assert row is not None and row.content_md == "新去重提示词" and row.version == 2
    hist = ps.list_history(db_session, "dedup_v1")
    assert [h.version for h in hist] == [2, 1]  # 倒序


def test_edit_no_change_keeps_version(db_session: Session) -> None:
    ps.import_prompts_from_files(db_session)
    row = ps.get_prompt_row(db_session, "dedup_v1")
    assert row is not None
    same = row.content_md
    v = ps.edit_prompt(db_session, "dedup_v1", same, operator="user:boss")
    assert v == 1  # 无变化不升版


def test_edit_missing_raises(db_session: Session) -> None:
    with pytest.raises(ps.PromptEditError, match="not found"):
        ps.edit_prompt(db_session, "nope", "x", operator="user:boss")


def test_rollback(db_session: Session) -> None:
    ps.import_prompts_from_files(db_session)
    orig = ps.get_prompt_row(db_session, "classify_v2").content_md  # type: ignore[union-attr]
    ps.edit_prompt(db_session, "classify_v2", "改坏了", operator="user:boss")  # v2
    new_v = ps.rollback_prompt(db_session, "classify_v2", 1, operator="user:boss")  # v3 = v1 内容
    assert new_v == 3
    row = ps.get_prompt_row(db_session, "classify_v2")
    assert row is not None and row.content_md == orig


def test_rollback_missing_version_raises(db_session: Session) -> None:
    ps.import_prompts_from_files(db_session)
    with pytest.raises(ps.PromptEditError, match="history version"):
        ps.rollback_prompt(db_session, "classify_v2", 99, operator="user:boss")

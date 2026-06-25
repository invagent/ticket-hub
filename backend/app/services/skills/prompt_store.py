"""Prompt store (D4 优化 v2) — 提示词 DB 覆盖 + 文件兜底 + 版本编辑.

load_prompt(name) 解析顺序：
    1. skill_prompts 表有该 name → 用 DB content（version 缓存失效）
    2. 否则回落 prompts/{name}.md 文件
    3. 都没有 → FileNotFoundError

DB 读任何异常（表不存在/连接失败，如单测环境）→ 静默回落文件，保证存量行为不变。
每次 load 开一个短 session 查该行（LLM 调用非热点，单条索引查询代价可忽略）；
`_cache` 仅作「DB 临时不可用且文件也没有」时的兜底，不是性能缓存。

编辑/回滚（admin）：edit_prompt / rollback_prompt。
seed：import_prompts_from_files 把 prompts/*.md 灌入表（幂等，已存在不覆盖）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db import make_session
from app.models import SkillPrompt, SkillPromptHistory

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# 进程内缓存：name -> (version, content)。version=-1 表示来自文件（无 DB 行）。
_cache: dict[str, tuple[int, str]] = {}


class PromptNotFoundError(Exception):
    """name 既无 DB 行也无对应文件。"""


def _file_content(name: str) -> str | None:
    f = _PROMPTS_DIR / f"{name}.md"
    if f.exists():
        return f.read_text(encoding="utf-8")
    return None


def load_prompt(name: str) -> str:
    """读提示词正文：DB 覆盖 → 文件兜底。DB 异常静默回落文件。"""
    try:
        with make_session() as db:
            row = db.execute(
                select(SkillPrompt.version, SkillPrompt.content_md).where(SkillPrompt.name == name)
            ).first()
        if row is not None:
            version, content = int(row[0]), str(row[1])
            _cache[name] = (version, content)
            return content
    except Exception:  # 表不存在/连接失败（单测）→ 文件兜底
        logger.debug("prompt_store_db_unavailable_fallback_file", name=name)

    file_content = _file_content(name)
    if file_content is None:
        # DB 也没有、文件也没有；但缓存里可能有旧 DB 值（DB 临时不可用时兜底）
        cached = _cache.get(name)
        if cached is not None:
            return cached[1]
        raise PromptNotFoundError(f"prompt {name!r} not in DB nor prompts/{name}.md")
    return file_content


def clear_cache() -> None:
    _cache.clear()


# ---- admin 操作（编辑/回滚/预览/导入）---------------------------------------


@dataclass(slots=True, frozen=True)
class PromptInfo:
    name: str
    type: str
    editable: bool
    version: int
    description: str | None
    updated_by: str | None
    updated_at: datetime | None


def list_prompts(db: Session) -> list[PromptInfo]:
    rows = db.execute(select(SkillPrompt).order_by(SkillPrompt.name)).scalars().all()
    return [
        PromptInfo(
            name=r.name,
            type=r.type,
            editable=r.editable,
            version=r.version,
            description=r.description,
            updated_by=r.updated_by,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


def get_prompt_row(db: Session, name: str) -> SkillPrompt | None:
    return db.execute(select(SkillPrompt).where(SkillPrompt.name == name)).scalar_one_or_none()


class PromptEditError(Exception):
    """编辑/回滚不可执行。"""


def edit_prompt(db: Session, name: str, content_md: str, *, operator: str, reason: str = "") -> int:
    """更新提示词正文，version+1，旧版进 history。返回新 version。Commits。"""
    content_md = content_md.strip()
    if not content_md:
        raise PromptEditError("content is empty")
    row = get_prompt_row(db, name)
    if row is None:
        raise PromptEditError(f"prompt {name!r} not found (先 import-from-files)")
    if not row.editable or row.type != "llm":
        raise PromptEditError(f"prompt {name!r} is not editable")
    if content_md == row.content_md:
        return row.version  # 无变化不升版
    new_version = row.version + 1
    db.add(
        SkillPromptHistory(
            name=name,
            version=new_version,
            content_md=content_md,
            changed_by=operator,
            reason=reason or "edit",
        )
    )
    row.content_md = content_md
    row.version = new_version
    row.updated_by = operator
    db.commit()
    clear_cache()
    logger.info("prompt_edited", name=name, version=new_version, operator=operator)
    return new_version


def rollback_prompt(db: Session, name: str, target_version: int, *, operator: str) -> int:
    """回滚到某历史 version 的内容（作为新版本写入，不丢历史）。返回新 version。"""
    row = get_prompt_row(db, name)
    if row is None:
        raise PromptEditError(f"prompt {name!r} not found")
    hist = db.execute(
        select(SkillPromptHistory).where(
            SkillPromptHistory.name == name, SkillPromptHistory.version == target_version
        )
    ).scalar_one_or_none()
    if hist is None:
        raise PromptEditError(f"history version {target_version} not found for {name!r}")
    return edit_prompt(
        db, name, hist.content_md, operator=operator, reason=f"rollback to v{target_version}"
    )


def list_history(db: Session, name: str) -> list[SkillPromptHistory]:
    return list(
        db.execute(
            select(SkillPromptHistory)
            .where(SkillPromptHistory.name == name)
            .order_by(SkillPromptHistory.version.desc())
        )
        .scalars()
        .all()
    )


def import_prompts_from_files(db: Session) -> int:
    """把 prompts/*.md 灌入 skill_prompts（幂等：已存在的 name 跳过）。返回新增数。"""
    added = 0
    now = datetime.now(UTC)
    for f in sorted(_PROMPTS_DIR.glob("*.md")):
        name = f.stem
        exists = get_prompt_row(db, name)
        if exists is not None:
            continue
        # strip 与 edit_prompt 一致，避免「同内容再编辑」因尾部空白误判有变化
        content = f.read_text(encoding="utf-8").strip()
        db.add(
            SkillPrompt(
                name=name,
                type="llm",
                editable=True,
                content_md=content,
                version=1,
                description=f"imported from prompts/{name}.md",
                updated_by="system:import",
            )
        )
        db.add(
            SkillPromptHistory(
                name=name,
                version=1,
                content_md=content,
                changed_by="system:import",
                reason="initial import",
                changed_at=now,
            )
        )
        added += 1
    db.commit()
    clear_cache()
    logger.info("prompts_imported_from_files", added=added)
    return added

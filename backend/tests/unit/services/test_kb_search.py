"""飞书知识库检索测试（ADR-0016 P3）— IDF 加权 bigram + 阈值 + 检索缺失=空.

不打网络：monkeypatch `_load_docs` 注入假文档集，专测排序/阈值/IDF 逻辑。
"""

from __future__ import annotations

import pytest

from app.services.knowledge_feedback import kb_search as kb


def _doc(token: str, title: str, content: str) -> kb.KbDoc:
    return kb.KbDoc(
        node_token=token, title=title, content=content, url=f"u/{token}", char_count=len(content)
    )


_DOCS = [
    _doc(
        "f1",
        "开票超时排查FAQ",
        "开票提示认证超时，实名已完成仍失败。多为税局通道拥堵所致，无需重新实名。",
    ),
    _doc("f2", "数电发票开票额度说明", "数电发票提示超出可用额度，每月上限500万元，次月恢复。"),
    _doc("f3", "发票红冲操作指引", "如何对已开具的数电发票做红冲，发起红字确认单后开红字发票。"),
]


@pytest.fixture(autouse=True)
def _inject_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kb, "_load_docs", lambda *, force=False: list(_DOCS))


def test_skill_case_hits_faq() -> None:
    hits = kb.search_kb("开票一直提示认证超时，实名做完了")
    assert hits, "应命中"
    assert hits[0].doc.node_token == "f1"  # 开票超时FAQ 居首
    assert "超时" in hits[0].snippet or "通道" in hits[0].snippet


def test_knowledge_case_hits_quota() -> None:
    hits = kb.search_kb("数电票超出额度，才开300万")
    assert hits[0].doc.node_token == "f2"  # 额度说明居首


def test_retrieval_case_empty() -> None:
    """冷门主题（知识库无该条目）→ 返回空 = 检索缺失强信号。"""
    hits = kb.search_kb("发票能不能批量导出成Excel表格")
    assert hits == [], f"应为空（检索缺失），实得 {[h.doc.title for h in hits]}"


def test_common_bigram_alone_does_not_match() -> None:
    """只含公共词「发票」的 query 不应把所有文档拉出来（IDF 压制）。"""
    hits = kb.search_kb("发票")
    assert hits == []


def test_empty_query_returns_empty() -> None:
    assert kb.search_kb("") == []
    assert kb.search_kb("   ") == []


def test_limit_caps_results() -> None:
    hits = kb.search_kb("开票超时额度红冲发票", limit=2)
    assert len(hits) <= 2


def test_kb_status_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("FEISHU_WIKI_SPACE_ID", "")
    get_settings.cache_clear()
    st = kb.kb_status()
    assert st.configured is False and st.doc_count is None
    get_settings.cache_clear()

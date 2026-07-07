"""飞书知识库检索（ADR-0016 P3 反思闭环 · 知识库/检索病因诊断的在-app 依据）.

反思工作台在 knowledge/retrieval 病因下需要看：知识库里有没有覆盖正解的条目？
本模块遍历 `FEISHU_WIKI_SPACE_ID` 空间树、读每篇 docx 正文，按失败问题做
**字符 bigram 重叠**检索（适配中文、小库够用），返回命中条目 + 片段 + 飞书链接。
空结果 = 检索缺失（retrieval）；命中但内容过时/错 = knowledge；命中且对但 AI 没
用好 = skill——三态一眼可辨。

只读：只 list/read，绝不写。遍历+读正文较慢，故整库文档缓存 TTL（默认 5min），
force 可强刷（KB 改完复查用）。写飞书是人工在飞书里做，我们只读来验证。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 相关度下限：低于此分视为「无关」——保证「知识库无该主题」时返回空（retrieval 缺失
# 的强信号），而非被「发票/开票」这类公共 bigram 拉出一堆低相关条目。
_MIN_SCORE = 3.0

_CACHE_TTL_SECONDS = 300.0
# (fetched_at, docs) —— 单空间，模块级缓存足够
_cache: tuple[float, list[KbDoc]] | None = None


@dataclass(slots=True, frozen=True)
class KbDoc:
    node_token: str
    title: str
    content: str
    url: str
    char_count: int


@dataclass(slots=True, frozen=True)
class KbHit:
    doc: KbDoc
    score: float
    snippet: str


class KbDisabledError(Exception):
    """未配置 FEISHU_WIKI_SPACE_ID —— 调用方映射 503。"""


def _link_base() -> str:
    base = (get_settings().feishu_wiki_link_base or "").rstrip("/")
    return base


def _load_docs(*, force: bool = False) -> list[KbDoc]:
    """遍历空间树 + 读正文，带 TTL 缓存。失败抛底层异常（调用方降级）。"""
    global _cache
    settings = get_settings()
    space = settings.feishu_wiki_space_id
    if not space:
        raise KbDisabledError("FEISHU_WIKI_SPACE_ID 未配置")
    now = time.monotonic()
    if not force and _cache is not None and (now - _cache[0]) < _CACHE_TTL_SECONDS:
        return _cache[1]

    from adapters.feishu import FeishuClient, FeishuConfig

    client = FeishuClient(FeishuConfig.from_settings(settings))
    base = _link_base()
    docs: list[KbDoc] = []
    try:
        # 遍历整个知识空间（FEISHU_WIKI_ROOT_NODE 是用户给的单篇链接节点，多为叶子，
        # 不能当遍历起点；整库检索走 space 根）
        nodes = client.walk_wiki_tree(space)
        for n in nodes:
            if n.obj_type != "docx" or not n.obj_token:
                continue
            try:
                content = client.get_doc_raw_content(n.obj_token)
            except Exception as e:  # 单篇失败不拖垮整库
                logger.warning("kb_doc_read_failed", node=n.node_token, error=str(e)[:120])
                content = ""
            docs.append(
                KbDoc(
                    node_token=n.node_token,
                    title=n.title,
                    content=content,
                    url=f"{base}/wiki/{n.node_token}" if base else "",
                    char_count=len(content),
                )
            )
    finally:
        client.close()
    _cache = (now, docs)
    logger.info("kb_docs_loaded", space=space, count=len(docs))
    return docs


def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in text if not ch.isspace())
    return {t[i : i + 2] for i in range(len(t) - 1)} if len(t) >= 2 else set(t)


def _snippet(content: str, query_grams: set[str], width: int = 80) -> str:
    """取正文中第一个命中 bigram 附近的窗口。"""
    flat = content.replace("\n", " ")
    for i in range(len(flat) - 1):
        if flat[i : i + 2] in query_grams:
            start = max(0, i - width // 3)
            return ("…" if start > 0 else "") + flat[start : start + width].strip() + "…"
    return flat[:width].strip() + ("…" if len(flat) > width else "")


def search_kb(query: str, *, limit: int = 5, force: bool = False) -> list[KbHit]:
    """按 query 做 IDF 加权 bigram 重叠检索。

    公共 bigram（出现在多数文档，如「发票」「开票」）经 idf=log(N/df) 权重趋零，
    只有区分性 bigram（「超时」「额度」）主导评分——低于 `_MIN_SCORE` 判为无关，
    保证冷门主题返回空（= retrieval 缺失）。标题命中额外 ×3。
    """
    docs = _load_docs(force=force)
    qg = _bigrams(query or "")
    if not qg or not docs:
        return []
    n = len(docs)
    doc_grams = [(d, _bigrams(d.title), _bigrams(d.content)) for d in docs]
    # df：每个 query bigram 出现在多少文档（标题∪正文）
    df: dict[str, int] = {}
    for g in qg:
        df[g] = sum(1 for _, tg, bg in doc_grams if g in tg or g in bg)
    idf = {g: math.log((n + 1) / (df[g] + 0.5)) for g in qg}

    hits: list[KbHit] = []
    for d, tg, bg in doc_grams:
        score = 0.0
        for g in qg & (tg | bg):
            score += idf[g] * (3.0 if g in tg else 1.0)
        if score < _MIN_SCORE:
            continue
        hits.append(KbHit(doc=d, score=round(score, 2), snippet=_snippet(d.content, qg)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


@dataclass(slots=True, frozen=True)
class KbStatus:
    configured: bool
    space_id: str
    doc_count: int | None
    error: str | None


def kb_status() -> KbStatus:
    """轻量状态：是否配了空间 + 能否遍历 + 文档数。失败不抛，塞 error。"""
    settings = get_settings()
    space = settings.feishu_wiki_space_id
    if not space:
        return KbStatus(configured=False, space_id="", doc_count=None, error=None)
    try:
        docs = _load_docs()
        return KbStatus(configured=True, space_id=space, doc_count=len(docs), error=None)
    except Exception as e:
        return KbStatus(configured=True, space_id=space, doc_count=None, error=str(e)[:200])

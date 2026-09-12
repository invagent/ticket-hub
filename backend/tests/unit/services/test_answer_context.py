"""Regression tests for context lost between ingestion and replay."""

from unittest.mock import Mock, patch

from app.config import Settings
from app.core.llm_router.vision import VisionError, VisionResult
from app.models import Attachment, HubIssue, ProductLine, Source, Ticket
from app.services.ai_cs.context import (
    build_hub_question,
    content_text_and_images,
    extract_image_context,
    format_question,
)


def seed(db):
    db.add(Source(code="ksm", name="KSM"))
    db.add(ProductLine(code="PROLINE6055", name="星瀚-开票"))
    db.flush()
    t = Ticket(
        short_code="TKT-context",
        source_code="ksm",
        source_ticket_id="ctx-1",
        type="Raw",
        status="processing",
        title="开票合并失败",
        body="正文",
        product_line_code="PROLINE6055",
    )
    db.add(t)
    db.flush()
    h = HubIssue(
        short_code="HUB-context",
        ticket_id=t.id,
        type="Operation",
        status="created",
        title=t.title,
        canonical_body=t.body,
        product_line_code=t.product_line_code,
    )
    db.add(h)
    db.flush()
    t.hub_issue_id = h.id
    return h, t


def test_product_code_resolved_and_title_not_lost(db_session):
    h, _ = seed(db_session)
    q = build_hub_question(db_session, h, settings=Settings())
    assert "产品线：星瀚-开票" in q
    assert "产品编码：PROLINE6055" in q
    assert "【工单标题】\n开票合并失败" in q
    assert "【问题正文】\n正文" in q


def test_missing_title_and_image_only_input():
    q = format_question(body='<img src="https://example.com/1.png">', images=["图片1：开票失败"])
    assert "【工单标题】" not in q
    assert "【问题正文】" not in q
    assert "【补充信息—图片提取】" in q
    assert "开票失败" in q
    assert "需要客户补充" in format_question()


def test_html_markdown_and_plain_links_deduplicated():
    text, urls = content_text_and_images(
        '<p>金额&lt;100，失败</p><img src="https://example.com/a.png?x=1&amp;y=2">'
        "![截图](https://example.com/a.png?x=1&y=2) https://example.com/b.jpg"
        "<script>do not include</script>"
    )
    assert "金额<100" in text
    assert "do not include" not in text
    assert urls == ["https://example.com/a.png?x=1&y=2", "https://example.com/b.jpg"]


def test_late_ocr_is_in_answer_even_when_hub_snapshot_is_stale(db_session):
    h, t = seed(db_session)
    t.body = "正文\n客户新补充：数量为2"
    att = Attachment(
        ticket_id=t.id,
        kind="image",
        source_url="https://example.com/a.png",
        vision_status="extracted",
        extracted_text="图中报错：负数无法冲抵",
    )
    db_session.add(att)
    db_session.flush()
    with patch("app.services.ai_cs.context.VisionClient.from_settings") as vision:
        q = build_hub_question(db_session, h, settings=Settings())
    vision.assert_not_called()
    assert "客户新补充：数量为2" in q
    assert "图中报错：负数无法冲抵" in q.split("【补充信息—图片提取】")[1]
    assert h.canonical_body == "正文"


def test_inline_image_extracted_before_answer_and_cached(db_session):
    h, t = seed(db_session)
    h.canonical_body = t.body = '<img src="https://example.com/new.png">'
    fake = Mock()
    fake.extract.return_value = VisionResult("E101", "开票页", "金额模糊", "test-vl", 0.0, {})
    settings = Settings(vision_enabled=True)
    with patch("app.services.ai_cs.context.VisionClient.from_settings", return_value=fake):
        first = build_hub_question(db_session, h, settings=settings)
        second = build_hub_question(db_session, h, settings=settings)
    assert "E101" in first and "E101" in second
    assert "开票页" in first and "金额模糊" in first
    assert fake.extract.call_count == 1
    assert db_session.query(Attachment).count() == 1


def test_stored_image_reads_bytes_and_reuses_cached_duplicate():
    store, vision = Mock(), Mock()
    store.key_from_storage_url.return_value = "ksm/1/a.png"
    store.get_bytes.return_value = b"image"
    vision.extract.return_value = VisionResult("错误", "界面", "摘要", "test-vl", 0.0, {})
    a = Attachment(
        id=1,
        ticket_id=1,
        source_url="https://example.com/a.png",
        storage_key="http://minio/bucket/ksm/1/a.png",
        kind="image",
    )
    result = extract_image_context(
        attachments=[a],
        urls=[a.source_url],
        settings=Settings(vision_enabled=True),
        vision_client=vision,
        store=store,
    )
    assert len(result) == 1
    assert vision.extract.call_args.kwargs["image_bytes"] == b"image"
    store.get_bytes.assert_called_once_with("ksm/1/a.png", max_bytes=10 * 1024 * 1024)
    assert a.vision_status == "extracted"
    vision.reset_mock()
    extract_image_context(
        attachments=[a], urls=[a.source_url], settings=Settings(), vision_client=vision
    )
    vision.extract.assert_not_called()


def test_missing_key_and_failed_images_are_explicit():
    with patch(
        "app.services.ai_cs.context.VisionClient.from_settings", side_effect=VisionError("key")
    ):
        items = extract_image_context(
            attachments=[],
            urls=["https://example.com/a.png"],
            settings=Settings(vision_enabled=True),
        )
    assert "未识别" in items[0] and "未配置" in items[0]
    vision = Mock()
    vision.extract.side_effect = VisionError("secret signed url")
    items = extract_image_context(
        attachments=[],
        urls=["https://example.com/a.png"],
        settings=Settings(vision_enabled=True),
        vision_client=vision,
    )
    assert "内容未知" in items[0] and "secret" not in items[0]


def test_private_links_and_image_limit_do_not_trigger_calls():
    vision = Mock()
    items = extract_image_context(
        attachments=[],
        urls=["http://127.0.0.1/a.png", "https://example.com/b.png"],
        settings=Settings(vision_enabled=True, vision_max_images_per_ticket=1),
        vision_client=vision,
    )
    vision.extract.assert_not_called()
    assert "未识别" in items[0] and "上限" in items[1]


def test_other_subtask_attachment_not_included(db_session):
    h, t = seed(db_session)
    other = HubIssue(short_code="HUB-other", title="其他子问题", type="Operation", status="created")
    db_session.add(other)
    db_session.flush()
    db_session.add(
        Attachment(
            ticket_id=t.id,
            hub_issue_id=other.id,
            kind="image",
            vision_status="extracted",
            extracted_text="其他任务的截图",
        )
    )
    db_session.flush()
    assert "其他任务的截图" not in build_hub_question(db_session, h, settings=Settings())


def test_operation_replay_receives_resolved_context(db_session):
    from adapters.ai_cs.types import ReplayResult
    from app.services.agents.operation_answer import AnswerRoute, auto_answer_operation

    h, _ = seed(db_session)
    h.op_status = "processing"
    h.op_handler = "agent"
    db_session.commit()
    fake = Mock()
    fake.replay.return_value = ReplayResult("请检查开票规则及单据数据。", [], [], "trace")
    with (
        patch("app.services.agents.operation_answer.build_client", return_value=fake),
        patch(
            "app.services.agents.operation_answer._route_answer",
            return_value=AnswerRoute("transfer"),
        ),
    ):
        auto_answer_operation(
            db_session, h.id, settings=Settings(operation_auto_reply_enabled=True)
        )
    q = fake.replay.call_args.kwargs["question"]
    assert "星瀚-开票" in q and "开票合并失败" in q and "正文" in q


def test_public_query_uses_image_evidence_before_replay():
    from adapters.ai_cs.types import ReplayResult
    from app.services.ai_cs.query import AnswerRoute, answer_question

    fake, vision = Mock(), Mock()
    fake.replay.return_value = ReplayResult("已找到错误。", [], [], "trace")
    vision.extract.return_value = VisionResult("E123", "开票页", "报错", "test", 0.0, {})
    with (
        patch("app.services.ai_cs.query.build_client", return_value=fake),
        patch("app.services.ai_cs.query.route_answer", return_value=AnswerRoute("D")),
        patch("app.services.ai_cs.context.VisionClient.from_settings", return_value=vision),
    ):
        answer_question(
            title="",
            content='<img src="https://example.com/a.png">',
            product_category="星瀚-开票",
            settings=Settings(vision_enabled=True),
        )
    q = fake.replay.call_args.kwargs["question"]
    assert "【补充信息—图片提取】" in q and "E123" in q
    fake.close.assert_called_once()


def test_vision_workspace_endpoint_is_configurable():
    from app.core.llm_router.vision import VisionClient

    settings = Settings(vision_api_key="test", vision_base_url="https://workspace.example/v1/")
    with patch("app.core.llm_router.vision.get_settings", return_value=settings):
        client = VisionClient.from_settings()
    assert client.base_url == "https://workspace.example/v1"
    assert client.model == "qwen-vl-max"

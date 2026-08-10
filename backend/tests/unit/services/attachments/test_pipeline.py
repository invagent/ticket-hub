from unittest.mock import MagicMock

from app.models import Attachment, Ticket
from app.services.attachments.pipeline import drain_pending_attachments


def _mk_ticket(db):
    t = Ticket(
        type="Raw",
        source_code="ksm",
        source_ticket_id="B1",
        short_code="T-1",
        title="t",
        body="orig",
        status="received",
    )
    db.add(t)
    db.flush()
    return t


def _mk_att(db, ticket_id, **kw):
    base = {
        "ticket_id": ticket_id,
        "source_url": "http://k/a.png",
        "kind": "image",
        "vision_status": "queued",
    }
    base.update(kw)
    a = Attachment(**base)
    db.add(a)
    db.flush()
    return a


def _mocks(img=b"PNGDATA", ocr_text="识别文本"):
    ksm = MagicMock()
    ksm.download_attachment.return_value = img
    store = MagicMock()
    store.put_bytes.return_value = "http://cdn/ksm/1/1_a.png"
    vision = MagicMock()
    vision.extract.return_value = MagicMock(
        ocr_text=ocr_text, ui_context="", summary="", model="qwen-vl-max", cost_usd=0.011
    )
    return ksm, store, vision


def test_happy_path_extracts_and_appends_body(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.extracted == 1 and rep.failed == 0
    db_session.refresh(a)
    db_session.refresh(t)
    assert a.vision_status == "extracted"
    assert a.storage_key is not None
    assert a.extracted_text == "识别文本"
    assert "识别文本" in t.body  # 追加到 body


def test_dry_run_skips(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=True)
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.skipped == 1
    ksm.download_attachment.assert_not_called()
    # dry_run 不改行状态——留 queued，翻掉 dry_run 后仍会被扫到真正处理（不永久卡 skipped）
    db_session.refresh(a)
    assert a.vision_status == "queued"


def test_oversize_skips(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False, max_bytes=3)
    t = _mk_ticket(db_session)
    _mk_att(db_session, t.id)
    ksm, store, vision = _mocks(img=b"TOOBIG")
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.skipped == 1
    store.put_bytes.assert_not_called()


def test_download_failure_retries_then_fails(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False, max_attempts=2)
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    ksm.download_attachment.side_effect = RuntimeError("net")
    # 第一轮：attempts=1，留 queued
    drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    db_session.refresh(a)
    assert a.download_attempts == 1 and a.vision_status == "queued"
    # 第二轮：attempts=2 达上限 → failed
    drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    db_session.refresh(a)
    assert a.vision_status == "failed" and a.last_error


def test_ocr_failure_after_upload_keeps_row_scannable_then_fails(db_session, monkeypatch):
    """Review 场景：upload 成功但 vision.extract 抛错。storage_key 不能被写入，
    否则 drain 的 storage_key IS NULL 过滤条件会把这行永久排除在外，卡成 stuck row。
    """
    _set_pipeline(monkeypatch, enabled=True, dry_run=False, max_attempts=2)
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    vision.extract.side_effect = RuntimeError("vision timeout")

    # 第一轮：upload 成功，OCR 失败 → attempts=1，storage_key 仍为 None，仍可被扫到。
    rep1 = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep1.scanned == 1
    assert rep1.failed == 0 and rep1.extracted == 0
    db_session.refresh(a)
    assert a.storage_key is None
    assert a.vision_status == "queued"
    assert a.download_attempts == 1
    store.put_bytes.assert_called_once()

    # 第二轮：row 依然被扫到（storage_key IS NULL 未被上一轮破坏）。
    rep2 = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep2.scanned == 1
    db_session.refresh(a)
    assert a.storage_key is None
    assert a.vision_status == "failed"
    assert a.download_attempts == 2
    assert "vision timeout" in (a.last_error or "")


def test_only_scans_queued(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    t = _mk_ticket(db_session)
    _mk_att(db_session, t.id, vision_status="pending")  # escalation 用，不该被扫
    _mk_att(db_session, t.id, vision_status="extracted")  # 终态
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.scanned == 0


def test_disabled_noop(db_session, monkeypatch):
    _set_pipeline(monkeypatch, enabled=False, dry_run=False)
    t = _mk_ticket(db_session)
    _mk_att(db_session, t.id)
    ksm, store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert rep.scanned == 0  # enabled 关 → 不扫


def test_minio_not_configured_fails_whole_batch(db_session, monkeypatch):
    """MinIO 未配置 → 整批 failed 转人工，绝不静默成功（store=None 触发默认构造抛错）。"""
    from app.core.storage.minio_store import MinioNotConfiguredError

    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    from app.services.attachments import pipeline as pipeline_mod

    monkeypatch.setattr(
        pipeline_mod, "MinioStore", MagicMock(side_effect=MinioNotConfiguredError("no keys"))
    )
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, _store, vision = _mocks()
    rep = drain_pending_attachments(db_session, ksm_client=ksm, store=None, vision_client=vision)
    assert rep.failed == 1
    db_session.refresh(a)
    assert a.vision_status == "failed"
    assert "minio_not_configured" in (a.last_error or "")
    ksm.download_attachment.assert_not_called()


def test_ensure_bucket_called_once_upfront(db_session, monkeypatch):
    """Task 2 review note: ensure_bucket 只在 drain 开头调一次，不是每行都调."""
    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    t = _mk_ticket(db_session)
    _mk_att(db_session, t.id)
    _mk_att(db_session, t.id, source_url="http://k/b.png")
    ksm, store, vision = _mocks()
    drain_pending_attachments(db_session, ksm_client=ksm, store=store, vision_client=vision)
    assert store.ensure_bucket.call_count == 1


def test_no_vision_key_stores_but_skips_ocr(db_session, monkeypatch):
    """无 vision key：附件仍下载+存 MinIO，跳过 OCR，落终态 skipped（不卡 queued、不报错）。"""
    from app.core.llm_router.vision import VisionError
    from app.services.attachments import pipeline as pipeline_mod

    _set_pipeline(monkeypatch, enabled=True, dry_run=False)
    # 模拟无 key：VisionClient.from_settings 抛错 → pipeline 降级 vision_client=None
    monkeypatch.setattr(
        pipeline_mod.VisionClient,
        "from_settings",
        classmethod(lambda cls: (_ for _ in ()).throw(VisionError("no vision API key"))),
    )
    t = _mk_ticket(db_session)
    a = _mk_att(db_session, t.id)
    ksm, store, _vision = _mocks()
    rep = drain_pending_attachments(
        db_session, ksm_client=ksm, store=store, vision_client=None
    )
    db_session.refresh(a)
    db_session.refresh(t)
    # 下载 + 存 MinIO 都发生了
    ksm.download_attachment.assert_called_once()
    store.put_bytes.assert_called_once()
    assert a.storage_key is not None  # 已存档
    # 但 OCR 跳过：终态 skipped、无识别文本、body 未追加
    assert a.vision_status == "skipped"
    assert a.last_error == "ocr_skipped_no_vision_key"
    assert not a.extracted_text
    assert t.body == "orig"
    assert rep.skipped == 1 and rep.extracted == 0 and rep.failed == 0


# helper：monkeypatch settings
def _set_pipeline(mp, *, enabled, dry_run, max_bytes=10 * 1024 * 1024, max_attempts=3):
    from app.services.attachments import pipeline as pipeline_mod

    s = MagicMock()
    s.attachment_pipeline_enabled = enabled
    s.attachment_pipeline_dry_run = dry_run
    s.attachment_max_bytes = max_bytes
    s.attachment_max_attempts = max_attempts
    s.vision_model = "qwen-vl-max"
    mp.setattr(pipeline_mod, "get_settings", lambda: s)

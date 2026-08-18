"""附件缩略图生成单测（app/services/attachments/thumbnail.py）。"""

from __future__ import annotations

import io

from PIL import Image

from app.services.attachments.thumbnail import THUMB_MIME, make_thumbnail


def _png_bytes(w: int, h: int, mode: str = "RGB") -> bytes:
    img = Image.new(mode, (w, h), (200, 100, 50) if mode == "RGB" else (200, 100, 50, 128))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def test_thumbnail_shrinks_large_image() -> None:
    data = _png_bytes(1000, 800)
    made = make_thumbnail(data)
    assert made is not None
    thumb_bytes, mime = made
    assert mime == THUMB_MIME
    # 最长边缩到 <= 240
    thumb = Image.open(io.BytesIO(thumb_bytes))
    assert max(thumb.size) <= 240
    # 宽高比保持（1000:800 = 5:4）
    assert abs(thumb.size[0] / thumb.size[1] - 1000 / 800) < 0.05
    # 缩略图字节远小于原图
    assert len(thumb_bytes) < len(data)


def test_thumbnail_handles_rgba_transparency() -> None:
    """带 alpha 的 PNG → 合成白底转 JPEG，不报错。"""
    data = _png_bytes(400, 400, mode="RGBA")
    made = make_thumbnail(data)
    assert made is not None
    thumb = Image.open(io.BytesIO(made[0]))
    assert thumb.mode == "RGB"  # JPEG 无 alpha


def test_thumbnail_non_image_returns_none() -> None:
    """非图片字节（如文本/损坏）→ None，调用方回落原图。"""
    assert make_thumbnail(b"this is not an image") is None
    assert make_thumbnail(b"") is None


def test_thumbnail_small_image_not_upscaled() -> None:
    """小于阈值的图不放大（thumbnail 只缩不放）。"""
    data = _png_bytes(100, 80)
    made = make_thumbnail(data)
    assert made is not None
    thumb = Image.open(io.BytesIO(made[0]))
    assert thumb.size == (100, 80)

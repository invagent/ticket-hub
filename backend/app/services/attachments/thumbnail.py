"""附件缩略图生成 —— 下载端点按需缩图（列表格子只需 ~96px，原图动辄数 MB）。

按需生成：MinIO 读原图仅数 ms，缩一张几十 ms，缩略图缓存回 MinIO 后后续直接命中，
对存量 / 新附件一视同仁（存量 storage_key 已落地，pipeline 不再扫，无法预生成）。
"""

from __future__ import annotations

import io

from PIL import Image

from app.core.logging import get_logger

logger = get_logger(__name__)

# 列表格子 96×96（object-cover），取 2x 清晰度上限 240px 最长边。
THUMB_MAX_EDGE = 240
THUMB_MIME = "image/jpeg"


def make_thumbnail(data: bytes, *, max_edge: int = THUMB_MAX_EDGE) -> tuple[bytes, str] | None:
    """把图片字节缩到最长边 ≤ max_edge，返回 (jpeg_bytes, mime)。

    非图片 / 解码失败 → None（调用方回落原图，绝不报错阻断下载）。
    统一输出 JPEG（体积小）；带 alpha 的（PNG 透明）先合成白底，避免 JPEG 转换报错。
    """
    try:
        opened = Image.open(io.BytesIO(data))
        opened.load()
    except Exception as e:  # 非图片 / 损坏 / 不支持的格式
        logger.info("thumbnail_decode_failed", error=str(e))
        return None

    try:
        # 有 alpha 通道（RGBA/LA/P 带透明）→ 合成白底转 RGB，否则 JPEG 保存报错。
        if opened.mode in ("RGBA", "LA") or (opened.mode == "P" and "transparency" in opened.info):
            bg = Image.new("RGB", opened.size, (255, 255, 255))
            rgba = opened.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        elif opened.mode != "RGB":
            img = opened.convert("RGB")
        else:
            img = opened

        img.thumbnail((max_edge, max_edge))  # 保持宽高比，原地缩小
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=80, optimize=True)
        return out.getvalue(), THUMB_MIME
    except Exception as e:
        logger.warning("thumbnail_encode_failed", error=str(e))
        return None

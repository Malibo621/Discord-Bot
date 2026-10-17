"""شعار السيرفر في كل الصور: موضع ثابت أعلى التصميم (بجانب العنوان).

يُمرَّر الشعار عبر ContextVar حتى لا تتغير توقيعات عشرات دوال الرسم؛ ويصل إلى خيط الرسم لأن
asyncio.to_thread/images.branded ينسخان السياق. أي خلل في الشعار (صورة تالفة/غياب) يعني ببساطة: لا شعار.
"""
from __future__ import annotations

import io
from contextvars import ContextVar
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw

BRAND: ContextVar[Optional[bytes]] = ContextVar("brand_icon", default=None)


@lru_cache(maxsize=16)
def circle(data: bytes, d_px: int) -> Optional[Image.Image]:
    """صورة الشعار دائرية بالحجم المطلوب (RGBA) أو None إن كانت البيانات غير صالحة."""
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA").resize((d_px, d_px), Image.LANCZOS)
    except Exception:  # noqa: BLE001
        return None
    mask = Image.new("L", (d_px * 3, d_px * 3), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d_px * 3 - 1, d_px * 3 - 1), fill=255)
    im.putalpha(mask.resize((d_px, d_px), Image.LANCZOS))
    return im


def current() -> Optional[bytes]:
    return BRAND.get()

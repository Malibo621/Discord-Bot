"""أساس التصميم الموحّد: لوحة رسم 16:9 بمقياس عالي الدقة + ألوان وثيمات + عناصر مشتركة.

كل البطاقات (الألعاب، الرصيد، الملف الشخصي، المتصدرون، المتجر، الموسيقى) تُبنى على هذه الطبقة، فتغيير
الألوان أو الخطوط أو الخلفيات يتم هنا مرة واحدة دون المساس بمنطق أي أمر.

الإحداثيات «منطقية» (640×360) وتُضرب تلقائيًا في SCALE=2 للحصول على صورة 1280×720 حادة. هذه النسبة 16:9
تُعرض أكبر وأوضح من الصور المربعة أو العريضة جدًا داخل ديسكورد، وتبقى مقروءة على الجوال عند التكبير.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFilter

from . import brand as _brand, fonts

SCALE = 2
LW, LH = 640, 360
W, H = LW * SCALE, LH * SCALE

# ---------------------------------------------------------------------- الألوان
BG = (15, 13, 30)
PANEL = (27, 24, 50)
WHITE = (255, 255, 255)
MUTED = (160, 156, 190)
SOFT = (214, 210, 236)
GOLD = (245, 192, 74)
GOLD_L = (255, 228, 150)
GREEN = (43, 214, 123)
RED = (255, 92, 122)
AMBER = (255, 181, 71)
BLUE = (79, 163, 255)
VIOLET = (124, 92, 255)
SHADOW = (0, 0, 0, 120)
HOLD_MS = 600_000          # آخر إطار GIF يبقى 10 دقائق (حد صيغة GIF ~10.9 دقيقة)
MAX_GIF_FRAMES = 24


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    accent: tuple
    accent2: tuple
    pattern: str


THEMES: dict[str, Theme] = {t.key: t for t in (
    Theme("math", "الحساب السريع", (79, 163, 255), (34, 195, 201), "grid"),
    Theme("typing", "أسرع كتابة", (43, 214, 123), (34, 195, 201), "lines"),
    Theme("quiz", "سؤال سريع", (166, 107, 255), (124, 92, 255), "dots"),
    Theme("fast", "اسرع", (255, 120, 84), (245, 192, 74), "lines"),
    Theme("guess", "خمّن الرقم", (255, 138, 76), (245, 192, 74), "rings"),
    Theme("scramble", "فكّك الكلمة", (255, 92, 122), (166, 107, 255), "tiles"),
    Theme("roulette", "الروليت", (245, 192, 74), (255, 138, 76), "rings"),
    Theme("balance", "المحفظة", (245, 192, 74), (255, 138, 76), "coins"),
    Theme("profile", "الملف الشخصي", (124, 92, 255), (79, 163, 255), "dots"),
    Theme("board", "المتصدرون", (245, 192, 74), (124, 92, 255), "rings"),
    Theme("shop", "المتجر", (43, 214, 123), (245, 192, 74), "tiles"),
    Theme("warnings", "سجل التحذيرات", (255, 92, 122), (255, 181, 71), "lines"),
    Theme("music", "الموسيقى", (29, 185, 84), (79, 163, 255), "lines"),
)}


# ---------------------------------------------------------------------- أدوات عامة
def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def px(v: float) -> int:
    return int(round(v * SCALE))


def text_width(text: str, size: float, weight: str = fonts.BOLD) -> float:
    return fonts.measure(text, px(size), weight) / SCALE


def fit_text(text: str, size: float, max_w: float, min_size: float = 10, ellipsis: bool = True,
             weight: str = fonts.BOLD) -> tuple[str, float]:
    """يصغّر الخط حتى يتسع النص في max_w، ثم يقصّ الكلمات الأخيرة بـ «…» إن لزم."""
    while size > min_size and text_width(text, size, weight) > max_w:
        size -= 1
    if ellipsis and text_width(text, size, weight) > max_w:
        words = text.split()
        while len(words) > 1 and text_width(" ".join(words) + "…", size, weight) > max_w:
            words.pop()
        text = " ".join(words) + "…"
        while len(text) > 2 and text_width(text, size, weight) > max_w:
            text = text[:-2].rstrip() + "…"
    return text, size


@lru_cache(maxsize=32)
def background(key: str) -> Image.Image:
    """الخلفية المشتركة لثيم: تدرج + نمط خفيف + توهج + إطار (تُخزَّن مؤقتًا وتُنسخ عند الاستخدام)."""
    t = THEMES[key]
    bg = Image.new("RGBA", (W, H), BG + (255,))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        d.line((0, y, W, y), fill=mix(BG, mix(BG, t.accent, 0.24), y / H) + (255,))
    pat = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pat)
    a = t.accent + (34,)
    rnd = random.Random(key)
    if t.pattern == "grid":
        for x in range(0, W, px(26)):
            pd.line((x, 0, x, H), fill=a)
        for y in range(0, H, px(26)):
            pd.line((0, y, W, y), fill=a)
    elif t.pattern == "lines":
        for i in range(-H, W, px(24)):
            pd.line((i, H, i + H, 0), fill=a, width=2)
    elif t.pattern == "dots":
        for x in range(px(14), W, px(24)):
            for y in range(px(14), H, px(24)):
                pd.ellipse((x - 3, y - 3, x + 3, y + 3), fill=a)
    elif t.pattern == "rings":
        for r in range(px(40), px(420), px(38)):
            pd.ellipse((W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r), outline=a, width=2)
    elif t.pattern == "coins":
        for _ in range(26):
            x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(px(10), px(26))
            pd.ellipse((x - r, y - r, x + r, y + r), outline=a, width=2)
            pd.ellipse((x - r * 0.6, y - r * 0.6, x + r * 0.6, y + r * 0.6), outline=a, width=1)
    else:  # tiles
        for _ in range(30):
            x, y, s = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(px(12), px(32))
            pd.rounded_rectangle((x, y, x + s, y + s), radius=px(4), outline=a, width=2)
    bg.alpha_composite(pat)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((W // 2 - px(190), H // 2 - px(100), W // 2 + px(190), H // 2 + px(120)),
                                 fill=t.accent + (70,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(px(48))))
    Canvas._rr_on(bg, (8, 8, LW - 8, LH - 8), 16, outline=t.accent + (150,), width=1.5)
    return bg


class Canvas:
    """لوحة رسم بإحداثيات منطقية (640×360) تُنتج صورة 1280×720."""

    def __init__(self, theme: str):
        self.theme = THEMES[theme]
        self.img = background(theme).copy()
        self.brand = _brand.current()       # شعار السيرفر (إن وُجد) من سياق الرسم

    # ---- أشكال
    @staticmethod
    def _rr_on(img: Image.Image, box, radius, fill=None, outline=None, width=1.0):
        x0, y0, x1, y1 = [px(v) for v in box]
        pad, k = 2, 3
        w, h = x1 - x0 + pad * 2, y1 - y0 + pad * 2
        if w <= 0 or h <= 0:
            return
        layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((pad * k, pad * k, (w - pad) * k - 1, (h - pad) * k - 1),
                                                radius=px(radius) * k, fill=fill, outline=outline,
                                                width=max(1, int(px(width) * k)))
        img.alpha_composite(layer.resize((w, h), Image.LANCZOS), (x0 - pad, y0 - pad))

    def rr(self, box, radius, fill=None, outline=None, width=1.0):
        self._rr_on(self.img, box, radius, fill, outline, width)

    def card(self, box, radius, fill=None, outline=None, width=1.0, shadow: bool = True):
        """لوحة رئيسية بظل ناعم وخط إضاءة علوي خفيف (هوية موحّدة لكل البطاقات)."""
        x0, y0, x1, y1 = box
        if shadow:
            pad = px(14)
            w, h = px(x1 - x0) + pad * 2, px(y1 - y0) + pad * 2
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(layer).rounded_rectangle((pad, pad + px(3), w - pad, h - pad + px(3)), radius=px(radius),
                                                    fill=(0, 0, 0, 120))
            self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(px(5))), (px(x0) - pad, px(y0) - pad))
        self.rr(box, radius, fill, outline, width)
        self.line((x0 + radius, y0 + 1.5), (x1 - radius, y0 + 1.5), (255, 255, 255, 30), 1)

    def pill(self, box, fill=None, outline=None, width=1.0):
        self.rr(box, (box[3] - box[1]) / 2, fill, outline, width)

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.0):
        k = 3
        d = px(r * 2) + 8
        layer = Image.new("RGBA", (d * k, d * k), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((4 * k, 4 * k, (d - 4) * k - 1, (d - 4) * k - 1), fill=fill, outline=outline,
                                      width=max(1, int(px(width) * k)))
        self.img.alpha_composite(layer.resize((d, d), Image.LANCZOS), (px(cx) - d // 2, px(cy) - d // 2))

    def poly(self, pts, fill):
        ImageDraw.Draw(self.img).polygon([(px(x), px(y)) for x, y in pts], fill=fill)

    def line(self, p0, p1, fill, width=1.0):
        ImageDraw.Draw(self.img).line((px(p0[0]), px(p0[1]), px(p1[0]), px(p1[1])), fill=fill, width=max(1, px(width)))

    def glow(self, cx, cy, r, color, alpha=120, blur=24):
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((px(cx - r), px(cy - r), px(cx + r), px(cy + r)), fill=color + (alpha,))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(px(blur))))

    # ---- نصوص
    def text(self, text: str, x: float, y: float, size: float, fill=WHITE, anchor: str = "mm",
             max_w: Optional[float] = None, shadow: bool = True, weight: Optional[str] = None) -> float:
        """نص بنظام الخطوط الموحّد (DIN-like + عربي) مع ظل. الوزن تلقائي: خافت للنصوص الثانوية وعريض لغيرها."""
        if not text:
            return size
        if weight is None:
            weight = fonts.MEDIUM if tuple(fill[:3]) in (MUTED, SOFT) else fonts.BOLD
        if max_w:
            text, size = fit_text(text, size, max_w, weight=weight)
        d = ImageDraw.Draw(self.img)
        if shadow:
            fonts.draw(d, (px(x) + 2, px(y) + 3), text, px(size), SHADOW, anchor=anchor, weight=weight)
        fonts.draw(d, (px(x), px(y)), text, px(size), fill, anchor=anchor, weight=weight)
        return size

    def wrap(self, text: str, size: float, max_w: float, max_lines: int = 3) -> list[str]:
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cur and text_width(trial, size) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip() + "…"
        return lines

    # ---- عناصر مشتركة
    def header(self, reward: Optional[str] = None, event: bool = False, badge: Optional[str] = None,
               title: Optional[str] = None):
        """الشريط العلوي: عنوان القسم (يمين)، الجائزة/معلومة (يسار)، وشارة وسطى."""
        t = self.theme
        label = (title or t.title) + ("  ·  فعالية" if event else "")
        right = LW - 18
        logo = _brand.circle(self.brand, px(26)) if self.brand else None
        if logo is not None:
            self.circle(right - 13, 27, 16, None, t.accent + (230,), 1.5)
            self.img.alpha_composite(logo, (px(right - 13) - logo.width // 2, px(27) - logo.height // 2))
            right -= 38
        tw = text_width(label, 15) + 22
        self.pill((right - tw, 14, right, 40), t.accent + (60,), t.accent + (200,), 1)
        self.text(label, right - tw / 2, 27, 15, WHITE, shadow=False)
        if reward:
            rw = text_width(reward, 15) + 44
            self.pill((18, 14, 18 + rw, 40), GOLD + (40,), GOLD + (210,), 1)
            self.circle(34, 27, 8, GOLD + (255,), (255, 226, 140, 255), 1)
            self.circle(34, 27, 4, None, (190, 130, 30, 255), 1)
            self.text(reward, 18 + 44 + (rw - 44) / 2 - 12, 27, 15, (255, 232, 160), shadow=False)
        if badge:
            bw = text_width(badge, 13) + 22
            self.pill((LW / 2 - bw / 2, 15, LW / 2 + bw / 2, 39), (255, 255, 255, 28), (255, 255, 255, 70), 1)
            self.text(badge, LW / 2, 27, 13, MUTED, shadow=False)

    def timer(self, frac: float, left: int):
        t = self.theme
        x0, x1, y0, y1 = 56, LW - 28, 322, 338
        self.rr((x0, y0, x1, y1), 8, fill=(255, 255, 255, 30))
        frac = max(0.0, min(1.0, frac))
        if frac > 0.003:
            col = t.accent if frac > 0.5 else (mix(AMBER, t.accent, (frac - 0.25) / 0.25) if frac > 0.25 else RED)
            w = max(16, (x1 - x0) * frac)
            self.rr((x1 - w, y0, x1, y1), 8, fill=col + (255,))
        self.text(f"{max(0, left)}", 30, (y0 + y1) / 2, 20, WHITE, shadow=False)

    def avatar(self, data: Optional[bytes], cx: float, cy: float, d: float, ring=GOLD, fallback: str = "?"):
        """صورة دائرية بحلقة؛ عند غياب الصورة يُرسم حرف أول الاسم على لون الثيم."""
        r = d / 2
        self.circle(cx, cy, r + 3, None, ring + (255,), 3)
        size = px(d)
        av = None
        if data:
            try:
                av = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
            except Exception:  # noqa: BLE001 - صورة تالفة: نكمل بدون صورة
                av = None
        if av is None:
            av = Image.new("RGBA", (size, size), self.theme.accent + (255,))
            fonts.draw(ImageDraw.Draw(av), (size // 2, size // 2), fallback[:1] or "?", size * 0.5, WHITE, anchor="mm")
        mask = Image.new("L", (size * 3, size * 3), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
        av.putalpha(mask.resize((size, size), Image.LANCZOS))
        self.img.alpha_composite(av, (px(cx) - size // 2, px(cy) - size // 2))

    def stat(self, box, label: str, value: str, color=WHITE, value_size: float = 24):
        """بطاقة إحصاء صغيرة: عنوان خافت + قيمة كبيرة."""
        x0, y0, x1, y1 = box
        self.rr(box, 12, fill=(255, 255, 255, 22), outline=self.theme.accent + (120,), width=1)
        cx = (x0 + x1) / 2
        self.text(label, cx, y0 + 16, 12.5, MUTED, shadow=False, max_w=x1 - x0 - 14)
        self.text(value, cx, y0 + (y1 - y0) * 0.64, value_size, color, max_w=x1 - x0 - 16)

    def progress(self, box, frac: float, color=None):
        x0, y0, x1, y1 = box
        self.rr(box, (y1 - y0) / 2, fill=(255, 255, 255, 30))
        frac = max(0.0, min(1.0, frac))
        if frac > 0.003:
            w = max(y1 - y0, (x1 - x0) * frac)
            self.rr((x1 - w, y0, x1, y1), (y1 - y0) / 2, fill=(color or self.theme.accent) + (255,))

    def footer(self, text: str):
        self.text(text, LW / 2, LH - 14, 11, (255, 255, 255, 110), shadow=False, max_w=LW - 80)

    # ---- إخراج
    def png(self) -> bytes:
        return png_bytes(self.img)

    def copy(self) -> "Canvas":
        c = Canvas.__new__(Canvas)
        c.theme, c.img = self.theme, self.img.copy()
        return c


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def gif_bytes(frames: Sequence[Image.Image], durations: Sequence[int]) -> bytes:
    """GIF بلوحة ألوان مشتركة من إطارين (أول ووسط) لتقليل الحجم وتثبيت الألوان."""
    sheet = Image.new("RGB", (frames[0].width * 2, frames[0].height))
    sheet.paste(frames[0].convert("RGB"), (0, 0))
    sheet.paste(frames[len(frames) // 2].convert("RGB"), (frames[0].width, 0))
    pal = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    q = [f.convert("RGB").quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    buf = io.BytesIO()
    q[0].save(buf, "GIF", save_all=True, append_images=q[1:], duration=list(durations), loop=0, disposal=1)
    return buf.getvalue()


def available() -> bool:
    return fonts.available()

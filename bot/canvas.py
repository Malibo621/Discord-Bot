"""نظام التصميم الموحّد للصور: رموز التصميم (ألوان/مسافات/خطوط) + مكوّنات رسم قابلة لإعادة الاستخدام.

كل البطاقات (الألعاب، الرصيد، الملف الشخصي، المتصدرون، المتجر، الموسيقى، التحذيرات) تُبنى على هذه الطبقة، فتغيير
الهوية يتم هنا مرة واحدة دون المساس بمنطق أي أمر.

الإحداثيات «منطقية» (640×360) وتُضرب في SCALE=2 للحصول على صورة 1280×720 (16:9) حادة.

لغة التصميم (Nova):
  • صفحة فاتحة هادئة بنقاط نصف تدرجية في زاويتين، وحبر كحلي للنص، ولون مميّز واحد لكل نوع صورة.
  • «بطاقة» بيضاء بحد رفيع وظل ناعم للمعلومات، و«بطاقة بطل» كحلية داكنة للرقم أو العنصر الأهم فقط.
  • تسلسل خطي ثابت: عرض (رقم رئيسي) > عنوان > نص > تعليق، والاتجاه من اليمين لليسار (الأهم يمينًا).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

from PIL import Image, ImageDraw, ImageFilter

from . import brand as _brand, fonts

SCALE = 2
LW, LH = 640, 360
W, H = LW * SCALE, LH * SCALE

# ---------------------------------------------------------------------- رموز الألوان
BG0, BG1 = (247, 249, 253), (230, 236, 247)       # خلفية الصفحة (أعلى → أسفل)
INK = (14, 22, 44)                                # نص أساسي
INK2 = (76, 89, 117)                              # نص ثانوي
INK3 = (128, 140, 166)                            # تعليقات وعناصر خافتة
SURFACE = (255, 255, 255)
SUNK = (239, 243, 251)                            # بلاطة داخل بطاقة
LINE = (214, 221, 236)                            # حدود رفيعة
NAVY0, NAVY1 = (16, 24, 52), (38, 56, 112)        # بطاقة البطل (تدرج)
GOLD = (236, 168, 28)
GOLD_L = (255, 214, 102)
GOLD_D = (150, 92, 0)                             # ذهبي للنص على الأبيض
GOOD = (18, 160, 98)
WARN = (222, 122, 8)
BAD = (222, 52, 78)
BLUE = (47, 128, 237)
VIOLET = (124, 82, 240)
WHITE = (255, 255, 255)
HOLD_MS = 600_000          # آخر إطار GIF يبقى 10 دقائق (حد صيغة GIF ~10.9 دقيقة)
MAX_GIF_FRAMES = 24

# أسماء قديمة تُستخدم في بعض الملفات (توافق)
GREEN, RED, AMBER = GOOD, BAD, WARN
MUTED, SOFT, PANEL = INK3, INK2, SURFACE

# رموز المسافات
M = 24                     # هامش الصفحة
GAP = 12                   # مسافة بين البطاقات
R_CARD, R_TILE, R_CHIP = 20, 14, 99


@dataclass(frozen=True)
class Theme:
    key: str
    title: str
    accent: tuple
    accent2: tuple


THEMES: dict[str, Theme] = {t.key: t for t in (
    Theme("math", "الحساب السريع", (47, 128, 237), (20, 184, 166)),
    Theme("typing", "أسرع كتابة", (18, 160, 98), (20, 184, 166)),
    Theme("quiz", "سؤال سريع", (124, 82, 240), (47, 128, 237)),
    Theme("fast", "اسرع", (240, 98, 52), (236, 168, 28)),
    Theme("guess", "خمّن الرقم", (240, 130, 40), (236, 168, 28)),
    Theme("scramble", "فكّك الكلمة", (222, 52, 78), (124, 82, 240)),
    Theme("roulette", "الروليت", (236, 168, 28), (240, 130, 40)),
    Theme("balance", "المحفظة", (236, 168, 28), (240, 130, 40)),
    Theme("profile", "الملف الشخصي", (124, 82, 240), (47, 128, 237)),
    Theme("board", "المتصدرون", (236, 168, 28), (124, 82, 240)),
    Theme("shop", "المتجر", (18, 160, 98), (236, 168, 28)),
    Theme("warnings", "سجل التحذيرات", (222, 52, 78), (222, 122, 8)),
    Theme("music", "الموسيقى", (30, 180, 90), (47, 128, 237)),
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


def with_alpha(c, a: int) -> tuple:
    return tuple(c[:3]) + (a,)


@lru_cache(maxsize=32)
def background(key: str) -> Image.Image:
    """خلفية الصفحة: تدرج فاتح + لمسة لون النوع أعلى الصورة + نقاط نصف تدرجية في ركنين (تُخزَّن وتُنسخ)."""
    t = THEMES[key]
    bg = Image.new("RGBA", (W, H), BG0 + (255,))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        d.line((0, y, W, y), fill=mix(BG0, BG1, y / H) + (255,))
    tint = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(tint).ellipse((W - px(330), -px(210), W + px(80), px(80)), fill=t.accent + (46,))
    bg.alpha_composite(tint.filter(ImageFilter.GaussianBlur(px(46))))
    k = 2
    dots = Image.new("RGBA", (W * k, H * k), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dots)
    step, reach = px(10) * k, 0.40 * W * k
    for cx, cy in ((W * k, 0), (0, H * k)):
        for x in range(0, W * k, step):
            for y in range(0, H * k, step):
                f = 1 - math.hypot(x - cx, y - cy) / reach
                if f > 0.08:
                    r = max(0.8, f * step * 0.34)
                    dd.ellipse((x - r, y - r, x + r, y + r), fill=(112, 132, 170, int(34 + 56 * f)))
    bg.alpha_composite(dots.resize((W, H), Image.LANCZOS))
    return bg


@lru_cache(maxsize=24)
def _gradient(w: int, h: int, c0: tuple, c1: tuple) -> Image.Image:
    """تدرج قطري من c0 إلى c1 بحجم w×h."""
    mask = Image.linear_gradient("L").resize((w, h), Image.BILINEAR)
    base = Image.new("RGBA", (w, h), c0 + (255,))
    return Image.composite(Image.new("RGBA", (w, h), c1 + (255,)), base, mask)


class Canvas:
    """لوحة رسم بإحداثيات منطقية (640×360) تُنتج صورة 1280×720."""

    def __init__(self, theme: str):
        self.theme = THEMES[theme]
        self.img = background(theme).copy()
        self.brand = _brand.current()       # شعار السيرفر (إن وُجد) من سياق الرسم
        self.timer_style = False

    @property
    def accent(self):
        return self.theme.accent

    # ---- أشكال أساسية
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

    def circle(self, cx, cy, r, fill=None, outline=None, width=1.0):
        k = 3
        d = px(r * 2) + 8
        layer = Image.new("RGBA", (d * k, d * k), (0, 0, 0, 0))
        ImageDraw.Draw(layer).ellipse((4 * k, 4 * k, (d - 4) * k - 1, (d - 4) * k - 1), fill=fill, outline=outline,
                                      width=max(1, int(px(width) * k)))
        self.img.alpha_composite(layer.resize((d, d), Image.LANCZOS), (px(cx) - d // 2, px(cy) - d // 2))

    def poly(self, pts, fill):
        k = 3
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        x0, y0 = px(min(xs)) - 4, px(min(ys)) - 4
        w, h = px(max(xs)) - x0 + 8, px(max(ys)) - y0 + 8
        layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
        ImageDraw.Draw(layer).polygon([((px(x) - x0) * k, (px(y) - y0) * k) for x, y in pts], fill=fill)
        self.img.alpha_composite(layer.resize((w, h), Image.LANCZOS), (x0, y0))

    def line(self, p0, p1, fill, width=1.0):
        ImageDraw.Draw(self.img).line((px(p0[0]), px(p0[1]), px(p1[0]), px(p1[1])), fill=fill, width=max(1, px(width)))

    def shadow(self, box, radius, alpha=46, dy=4, blur=7):
        x0, y0, x1, y1 = box
        pad = px(blur * 2.2)
        w, h = px(x1 - x0) + pad * 2, px(y1 - y0) + pad * 2
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((pad, pad + px(dy), w - pad, h - pad + px(dy)), radius=px(radius),
                                                fill=(26, 40, 84, alpha))
        self.img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(px(blur))), (px(x0) - pad, px(y0) - pad))

    # ---- نصوص
    def text(self, text: str, x: float, y: float, size: float, fill=INK, anchor: str = "mm",
             max_w: Optional[float] = None, weight: Optional[str] = None, min_scale: float = 0.6, **_ignored) -> float:
        """نص بنظام الخطوط الموحّد. يصغَّر تلقائيًا (حتى min_scale) ثم يُقتطع بـ «…» عند ضيق المساحة."""
        if not text:
            return size
        if weight is None:
            weight = fonts.MEDIUM if tuple(fill[:3]) in (INK2, INK3) else fonts.BOLD
        if max_w:
            text, size = fit_text(text, size, max_w, min_size=max(9, size * min_scale), weight=weight)
        fonts.draw(ImageDraw.Draw(self.img), (px(x), px(y)), text, px(size), fill, anchor=anchor, weight=weight)
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
            lines[-1] = fit_text(lines[-1] + " …", size, max_w, min_size=size)[0]
        return lines

    # ---- مكوّنات
    def card(self, box, radius=R_CARD, fill=SURFACE, outline=LINE, width=1.0, shadow: bool = True, accent=None):
        """بطاقة بيضاء بحد رفيع وظل ناعم. accent: شريط لوني رفيع في أعلى البطاقة."""
        if shadow:
            self.shadow(box, radius)
        self.rr(box, radius, tuple(fill) + (255,) if len(fill) == 3 else fill,
                tuple(outline) + (255,) if outline and len(outline) == 3 else outline, width)
        if accent:
            x0, y0, x1, y1 = box
            self.rr((x0 + radius, y0, x1 - radius, y0 + 3), 1.5, fill=tuple(accent) + (255,))

    def hero(self, box, radius=22, accent=None):
        """بطاقة البطل: تدرج كحلي داكن مع توهج لوني خفيف — للرقم/العنصر الأهم فقط."""
        x0, y0, x1, y1 = box
        accent = accent or self.accent
        self.shadow(box, radius, alpha=70, dy=6, blur=9)
        w, h = px(x1 - x0), px(y1 - y0)
        panel = _gradient(w, h, NAVY0, NAVY1).copy()
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse((-w // 6, -h // 2, w // 2, h // 2), fill=tuple(accent) + (70,))
        panel.alpha_composite(glow.filter(ImageFilter.GaussianBlur(px(30))))
        mask = Image.new("L", (w * 3, h * 3), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 3 - 1, h * 3 - 1), radius=px(radius) * 3, fill=255)
        panel.putalpha(mask.resize((w, h), Image.LANCZOS))
        self.img.alpha_composite(panel, (px(x0), px(y0)))
        self.rr(box, radius, outline=(255, 255, 255, 38), width=1)

    def chip(self, text: str, x: float, y: float, anchor: str = "r", color=None, solid: bool = False,
             size: float = 12.5, h: float = 24, dot: bool = False) -> float:
        """شارة نصية صغيرة. تُرجع عرضها. anchor: r/l/c بالنسبة إلى x، y هو المركز الرأسي."""
        color = color or INK2
        tw = text_width(text, size, fonts.BOLD) + 20 + (12 if dot else 0)
        x0 = x - tw if anchor == "r" else (x if anchor == "l" else x - tw / 2)
        box = (x0, y - h / 2, x0 + tw, y + h / 2)
        if solid:
            self.rr(box, h / 2, fill=tuple(color) + (255,))
            fg = WHITE
        else:
            self.rr(box, h / 2, fill=tuple(color) + (30,), outline=tuple(color) + (120,), width=1)
            fg = mix(color, INK, 0.58) if sum(color) > 330 else color
        if dot:
            self.circle(x0 + tw - 13, y, 3.2, tuple(fg) + (255,))
            self.text(text, x0 + tw - 22, y, size, fg, anchor="rm")
        else:
            self.text(text, x0 + tw / 2, y, size, fg)
        return tw

    def _brand_mark(self, y: float) -> float:
        """الشعار (أو شريط لوني بديل) في أقصى اليمين. يرجع حافة النص اليمنى بعده."""
        t = self.theme
        right = LW - M
        logo = _brand.circle(self.brand, px(34)) if self.brand else None
        if logo is not None:
            self.circle(right - 17, y, 20, WHITE + (255,), t.accent + (255,), 2)
            self.img.alpha_composite(logo, (px(right - 17) - logo.width // 2, px(y) - logo.height // 2))
            return right - 50
        self.rr((right - 6, y - 17, right, y + 17), 3, fill=tuple(t.accent) + (255,))
        return right - 16

    def header(self, title: Optional[str] = None, subtitle: Optional[str] = None, chip: Optional[str] = None,
               reward: Optional[str] = None, event: bool = False, y: float = 34):
        """رأس موحّد: شعار السيرفر + عنوان كبير (يمين) وسطر فرعي صغير، وشارات في الطرف الآخر."""
        right = self._brand_mark(y)
        sub = subtitle
        if event:
            sub = f"{subtitle}  ·  فعالية" if subtitle else "فعالية"
        self.text(title or self.theme.title, right, y - (8 if sub else 0), 23, INK, anchor="rm", max_w=340)
        if sub:
            self.text(sub, right, y + 14, 12.5, INK3, anchor="rm", max_w=340)
        x = M
        if reward:
            x += self.chip(reward, x, y, "l", GOLD, size=13.5, h=28) + 8
        if chip:
            self.chip(chip, x, y, "l", INK2, size=12.5, h=26)

    def tile(self, box, label: str, value: str, color=INK, value_size: float = 24, fill=SUNK, raised: bool = False):
        """بلاطة إحصاء: تسمية صغيرة فوق قيمة كبيرة (مراكزة). raised=True: بطاقة بيضاء بظل (خارج البطاقات)."""
        x0, y0, x1, y1 = box
        if raised:
            self.card(box, R_TILE + 2)
        else:
            self.rr(box, R_TILE, fill=tuple(fill) + (255,))
        cx = (x0 + x1) / 2
        self.text(label, cx, y0 + (y1 - y0) * 0.30, 13.5, INK3, max_w=x1 - x0 - 14, weight=fonts.MEDIUM)
        self.text(value, cx, y0 + (y1 - y0) * 0.66, value_size, color, max_w=x1 - x0 - 16)

    def progress(self, box, frac: float, color=None, track=None):
        x0, y0, x1, y1 = box
        r = (y1 - y0) / 2
        self.rr(box, r, fill=tuple(track or (214, 222, 238)) + (255,))
        frac = max(0.0, min(1.0, frac))
        if frac > 0.003:
            w = max(y1 - y0, (x1 - x0) * frac)
            self.rr((x1 - w, y0, x1, y1), r, fill=tuple(color or self.accent) + (255,))

    def avatar(self, data: Optional[bytes], cx: float, cy: float, d: float, ring=None, fallback: str = "?"):
        """صورة دائرية بحلقة وظل خفيف؛ عند غياب الصورة يُرسم حرف أول الاسم على تدرج لون النوع."""
        r = d / 2
        ring = ring or self.accent
        self.circle(cx, cy + 2, r + 4, (26, 40, 84, 40))
        self.circle(cx, cy, r + 4, WHITE + (255,), tuple(ring) + (255,), 2.5)
        size = px(d)
        av = None
        if data:
            try:
                av = Image.open(io.BytesIO(data)).convert("RGBA").resize((size, size), Image.LANCZOS)
            except Exception:  # noqa: BLE001 - صورة تالفة: نكمل بدون صورة
                av = None
        if av is None:
            av = _gradient(size, size, self.theme.accent, self.theme.accent2).copy()
            fonts.draw(ImageDraw.Draw(av), (size // 2, size // 2), (fallback.strip()[:1] or "?"), size * 0.5, WHITE,
                       anchor="mm")
        mask = Image.new("L", (size * 3, size * 3), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
        av.putalpha(mask.resize((size, size), Image.LANCZOS))
        self.img.alpha_composite(av, (px(cx) - size // 2, px(cy) - size // 2))

    def coin(self, cx: float, cy: float, r: float = 9):
        self.circle(cx, cy, r, GOLD + (255,), mix(GOLD, WHITE, 0.55) + (255,), max(1.0, r / 7))
        self.circle(cx, cy, r * 0.5, None, GOLD_D + (230,), max(1.0, r / 8))

    def footer(self, text: str):
        self.text(text, LW / 2, LH - 13, 11, INK3, max_w=LW - 120, weight=fonts.MEDIUM)

    # ---- إطار الألعاب الموحّد: رأس + مؤقت mm:ss + شريط وقت
    def game_frame(self, title: Optional[str], reward: Optional[str], caption: Optional[str] = None,
                   event: bool = False):
        self.timer_style = True
        right = self._brand_mark(30)
        label = (title or self.theme.title) + ("  ·  فعالية" if event else "")
        self.text(label, right, 30, 23, INK, anchor="rm", max_w=330)
        if reward:
            self.chip(f"الجائزة {reward}", LW - M, 80, "r", GOLD, size=14.5, h=28)
        if caption:
            self.text(caption, M, 80, 15, INK2, anchor="lm", max_w=360, weight=fonts.MEDIUM)

    def timer(self, frac: float, left: int):
        t = self.theme
        if self.timer_style:
            left = max(0, left)
            self.card((M, 14, M + 84, 46), 12, shadow=False, outline=LINE)
            self.text(f"{left // 60:02d}:{left % 60:02d}", M + 42, 30.5, 19, INK)
            x0, x1, y0, y1 = M, LW - M, 54, 62
            self.rr((x0, y0, x1, y1), 4, fill=(213, 221, 237, 255))
            frac = max(0.0, min(1.0, frac))
            if frac > 0.003:
                col = t.accent if frac > 0.5 else (mix(WARN, t.accent, (frac - 0.25) / 0.25) if frac > 0.25 else BAD)
                self.rr((x1 - max(8, (x1 - x0) * frac), y0, x1, y1), 4, fill=tuple(col) + (255,))
            return
        x0, x1, y0, y1 = 56, LW - 28, 322, 338
        self.progress((x0, y0, x1, y1), frac, t.accent)
        self.text(f"{max(0, left)}", 30, (y0 + y1) / 2, 20, INK)

    # ---- إخراج
    def png(self) -> bytes:
        return png_bytes(self.img)

    def copy(self) -> "Canvas":
        c = Canvas.__new__(Canvas)
        c.__dict__.update(self.__dict__)
        c.img = self.img.copy()
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

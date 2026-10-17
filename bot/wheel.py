"""رسم عجلة الروليت: صورة ثابتة للانتظار، GIF متحرك للدوران، وصورة النتيجة.

مبدأ الموثوقية: الفائز يُختار في منطق اللعبة أولًا (choose_winner)، ثم تُحسب زاوية التوقف
(stop_angle) بحيث يقع المؤشر داخل قطاع الفائز نفسه. الأنيميشن مجرد عرض لنتيجة محسومة مسبقًا،
و segment_at() تثبت ذلك رياضيًا (ومغطاة بالاختبارات).

اصطلاح الزوايا: القطاع i يمتد من i*seg إلى (i+1)*seg درجة باتجاه عقارب الساعة بدءًا من الأعلى
(مكان المؤشر). تدوير العجلة بزاوية θ مع عقارب الساعة يجعل النقطة المحلية (−θ mod 360) تحت المؤشر.
"""
from __future__ import annotations

import io
import math
import os
import random
import secrets
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Sequence

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import brand as _brand, fonts

# ---------------------------------------------------------------------- الهوية البصرية
CW, CH = 960, 540          # أبعاد الصورة النهائية (16:9)
SS = 2                     # تكبير الرسم لتنعيم الحواف
R = 196                    # نصف قطر العجلة
CX, CY = CW // 2, 296      # مركز العجلة (في المنتصف، وعلى جانبيها اللوحات)
BG = (15, 13, 30)
BG_GLOW = (70, 46, 140)
RIM = (32, 26, 56)
GOLD = (245, 192, 74)
GOLD_L = (255, 228, 150)
GOLD_D = (172, 118, 30)
WHITE = (255, 255, 255)
# ألوان متناسقة (درجات الجواهر) مرتبة بحيث يتباين كل قطاع مع جاره
PALETTE = [
    (112, 84, 245),   # بنفسجي
    (46, 160, 255),   # أزرق سماوي
    (38, 198, 126),   # زمردي
    (247, 184, 58),   # كهرماني
    (255, 120, 84),   # مرجاني
    (236, 82, 140),   # وردي
    (28, 190, 200),   # فيروزي
    (150, 104, 255),  # بنفسجي فاتح
]
BULBS = 32
NAMED_MAX = 28             # أكثر من هذا العدد = أرقام فقط على العجلة (والأسماء في قائمة الرسالة)
HOLD_MS = 600_000          # آخر إطار يبقى 10 دقائق (حد صيغة GIF ~10.9 دقيقة) فلا يعيد التشغيل
MAX_GIF_BYTES = 5_500_000  # حد آمن لرفع ديسكورد (الافتراضي 8MB) وسرعة التحميل على الجوال
GIF_SCALE = 0.8            # الـ GIF أصغر قليلًا من PNG: ديسكورد يعرض الصورة المضمّنة بعرض ~400px أصلًا



# ---------------------------------------------------------------------- منطق النتيجة
_rng = secrets.SystemRandom()


def choose_winner(n: int, rng: Optional[random.Random] = None) -> int:
    if n <= 0:
        raise ValueError("no players")
    return (rng or _rng).randrange(n)


def segment_at(angle: float, n: int) -> int:
    """رقم القطاع الواقع تحت المؤشر عندما تكون العجلة مدوّرة بزاوية angle."""
    seg = 360 / n
    local = (-angle) % 360
    return min(n - 1, int(local // seg))


def stop_angle(winner: int, n: int, spins: int = 5, rng: Optional[random.Random] = None) -> float:
    """زاوية التوقف النهائية (موجبة، مع عدة لفات كاملة) التي تضع المؤشر داخل قطاع الفائز."""
    seg = 360 / n
    offset = (rng or _rng).uniform(0.22, 0.78)  # بعيدًا عن الحدود ليبقى الفائز واضحًا
    local = (winner + offset) * seg
    return spins * 360 + ((-local) % 360)


def spin_curve(frames: int, accel: float = 0.12) -> list[float]:
    """منحنى تقدم 0→1: تسارع ناعم (smoothstep) ثم تباطؤ تدريجي بلا قفزة في السرعة عند الانتقال."""
    samples = 600
    v = []
    for i in range(samples):
        t = i / (samples - 1)
        if t < accel:
            u = t / accel
            v.append(u * u * (3 - 2 * u))                  # بداية بطيئة ثم تزداد
        else:
            x = (t - accel) / (1 - accel)
            v.append((1 - x) ** 2.6)                       # تباطؤ يبدأ من أقصى سرعة بلا انقطاع
    cum, total = [0.0], 0.0
    for x in v[1:]:
        total += x
        cum.append(total)
    cum = [c / total for c in cum]
    return [cum[round(i * (samples - 1) / (frames - 1))] for i in range(frames)]


# ---------------------------------------------------------------------- النصوص (عبر نظام الخطوط الموحّد)
def has_text_font() -> bool:
    return fonts.available()


def clean_name(name: str, limit: int = 14) -> str:
    """يحذف الإيموجي والرموز غير المدعومة ويقص الاسم الطويل (يُستخدم قبل الرسم لتقدير العرض بدقة)."""
    kept = "".join(c for c in name if (unicodedata.category(c)[0] in "LNPMZ"
                                      or unicodedata.category(c) in ("Sc", "Sm")) and unicodedata.category(c) != "Cf")
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[: limit - 1].rstrip() + "…"
    return kept


def shape(text: str) -> str:
    return fonts.shape(text)


def _text_image(text: str, size: int, fill=WHITE, stroke=(0, 0, 0, 140)) -> Image.Image:
    return fonts.render(text, size, fill, stroke)


# ---------------------------------------------------------------------- الرسم
def _colors(n: int) -> list[tuple[int, int, int]]:
    cols = [PALETTE[i % len(PALETTE)] for i in range(n)]
    if n > 1 and cols[-1] == cols[0]:  # لا يتشابه أول قطاع وآخره
        for cand in PALETTE:
            if cand not in (cols[0], cols[-2] if n > 2 else cols[0]):
                cols[-1] = cand
                break
    return cols


def _shade(c, k: float):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def _labels(names: Sequence[str]) -> list[str]:
    n = len(names)
    if n > NAMED_MAX or not has_text_font():
        return [str(i + 1) for i in range(n)]
    limit = 18 if n <= 8 else 14 if n <= 16 else 10
    return [clean_name(nm, limit) or str(i + 1) for i, nm in enumerate(names)]


def _pill(img: Image.Image, box, fill, outline=None, width=2):
    """كبسولة منعّمة الحواف (ترسم بدقة مضاعفة على منطقتها فقط)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    pad, k = 2, 3
    w, h = x1 - x0 + pad * 2, y1 - y0 + pad * 2
    layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle((pad * k, pad * k, (w - pad) * k - 1, (h - pad) * k - 1),
                                            radius=(y1 - y0) * k // 2, fill=fill, outline=outline, width=width * k)
    img.alpha_composite(layer.resize((w, h), Image.LANCZOS), (x0 - pad, y0 - pad))


def _tw(text: str, size: int) -> int:
    return int(fonts.measure(text, size))


def _label_for_arc(label: str, n: int) -> tuple[str, int]:
    """يرجع (النص، حجم الخط) المناسبين لعرض القطاع وطول نصف القطر المتاح."""
    arc = R * 0.7 * (2 * math.pi / n)
    size = max(11, min(24, int(arc * 0.5)))
    avail = (R * 0.9 - R * 0.27)
    while size > 11 and _tw(label, size) > avail:
        size -= 1
    while len(label) > 2 and _tw(label, size) > avail:
        label = label[:-2].rstrip() + "…"
    return label, size


def _wheel(names: Sequence[str], dim_except: Optional[int] = None, muted: bool = False) -> Image.Image:
    """العجلة غير مدوّرة بحجم 2R: تدرجات شعاعية، فواصل، مسامير ذهبية، حلقة داخلية وأسماء.

    dim_except: إن حُدد رقم، يُعتم كل قطاع عداه ويُبرز هذا القطاع (حالة إعلان الفائز).
    """
    key = (tuple(names), dim_except, muted)
    cached = _WHEEL_CACHE.get(key)
    if cached is not None:
        return cached
    n = max(1, len(names))
    r = R * SS
    d = 2 * r
    seg = 360 / n
    cols = _colors(n)
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(n):
        col = cols[i] if not muted else _shade((120, 118, 150), 0.7 if i % 2 else 0.85)
        col = _shade(col, 0.9) if (i % 2 and not muted) else col  # تباين خفيف بين القطاعات المتجاورة
        if dim_except is not None and i != dim_except:
            gray = sum(col) // 3
            col = _shade(tuple((c + gray * 2) // 3 for c in col), 0.38)
        draw.pieslice((0, 0, d - 1, d - 1), -90 + i * seg, -90 + (i + 1) * seg, fill=col + (255,))

    # تدرج شعاعي: مركز أغمق وحافة أفتح
    grad = Image.radial_gradient("L").resize((d, d), Image.BILINEAR)
    dark = grad.point(lambda v: int(120 * max(0.0, 1 - v / 175) ** 1.4))
    light = grad.point(lambda v: int(44 * max(0.0, (v - 175) / 80)))
    black = Image.new("L", (d, d), 0)
    white = Image.new("L", (d, d), 255)
    img.alpha_composite(Image.merge("RGBA", (black, black, black, dark)))
    img.alpha_composite(Image.merge("RGBA", (white, white, white, light)))
    # لمعة زجاجية خفيفة في النصف العلوي
    gloss = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse((int(d * 0.12), int(d * -0.28), int(d * 0.88), int(d * 0.52)), fill=(255, 255, 255, 22))
    img.alpha_composite(gloss)

    draw = ImageDraw.Draw(img)
    if n > 1:
        for i in range(n):
            a = math.radians(-90 + i * seg)
            draw.line((r + r * 0.2 * math.cos(a), r + r * 0.2 * math.sin(a), r + r * math.cos(a), r + r * math.sin(a)),
                      fill=(255, 255, 255, 120), width=2 * SS)
            sx, sy = r + (r - 9 * SS) * math.cos(a), r + (r - 9 * SS) * math.sin(a)  # مسمار ذهبي عند كل فاصل
            draw.ellipse((sx - 4 * SS, sy - 4 * SS, sx + 4 * SS, sy + 4 * SS), fill=GOLD_L + (255,), outline=GOLD_D + (255,),
                         width=SS)
    # حلقتان زخرفيتان حول المركز
    for rad, wid in ((0.27, 3), (0.225, 1)):
        rr = r * rad
        draw.ellipse((r - rr, r - rr, r + rr, r + rr), outline=GOLD + (200,), width=wid * SS)

    # الأسماء: تبدأ من جهة الحافة نحو المركز، وتُقلب في النصف الأيسر لتبقى مقروءة
    if not muted:
        labels = _labels(names)
        for i, raw in enumerate(labels):
            label, size = _label_for_arc(raw, n)
            mid = -90 + (i + 0.5) * seg
            fill = WHITE if dim_except in (None, i) else (200, 200, 214)
            t = _text_image(label, size * SS, fill=fill, stroke=(0, 0, 0, 170))
            rot = -mid
            if 90 < (mid % 360) < 270:
                rot += 180
            t = t.rotate(rot, expand=True, resample=Image.BICUBIC)
            rad = math.radians(mid)
            length = max(t.width, t.height)  # تقريب مناسب بعد التدوير
            dist = r * 0.9 - _tw(label, size) * SS / 2
            cx, cy = r + dist * math.cos(rad), r + dist * math.sin(rad)
            img.alpha_composite(t, (int(cx - t.width / 2), int(cy - t.height / 2)))
    if dim_except is not None and n > 1:  # حدّ ذهبي حول قطاع الفائز
        box = (3, 3, d - 4, d - 4)
        draw.arc(box, -90 + dim_except * seg, -90 + (dim_except + 1) * seg, fill=GOLD_L + (255,), width=7 * SS)
        for a in (dim_except * seg, (dim_except + 1) * seg):
            rad = math.radians(-90 + a)
            draw.line((r + r * 0.27 * math.cos(rad), r + r * 0.27 * math.sin(rad), r + r * math.cos(rad),
                       r + r * math.sin(rad)), fill=GOLD_L + (255,), width=3 * SS)

    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    img.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    out = img.resize((2 * R, 2 * R), Image.LANCZOS)
    if len(_WHEEL_CACHE) > 12:
        _WHEEL_CACHE.clear()
    _WHEEL_CACHE[key] = out
    return out


_WHEEL_CACHE: dict = {}


def _count_label(n: int) -> str:
    if n == 0:
        return "لا مشاركين"
    if n == 1:
        return "مشارك واحد"
    if n == 2:
        return "مشاركان"
    return f"{n} مشاركين" if n <= 10 else f"{n} مشاركًا"


STATUS_STYLE = {
    "waiting": ("بانتظار المشاركين", (43, 214, 123)),
    "spinning": ("العجلة تدور…", (255, 181, 71)),
    "result": ("الفائز", GOLD),
}


LEGEND_ROWS = 12


def _legend_for(names: Sequence[str], winner: Optional[int] = None) -> tuple:
    """صفوف قائمة المشاركين بألوان قطاعاتهم: (التسمية، اللون، رقم القطاع، هل هو الفائز)."""
    n = len(names)
    cols = _colors(n) if n else []
    show = list(range(min(n, LEGEND_ROWS if n <= LEGEND_ROWS else LEGEND_ROWS - 1)))
    if winner is not None and winner not in show and show:
        show[-1] = winner                      # الفائز يظهر دائمًا في القائمة حتى مع العدد الكبير
    numbered = n > NAMED_MAX
    rows = []
    for i in show:
        label = clean_name(names[i], 20) or f"#{i + 1}"
        rows.append((f"{i + 1}  {label}" if numbered else label, cols[i], i, i == winner))
    hidden = n - len(show)
    return tuple(rows), hidden


@lru_cache(maxsize=24)
def _scene(status: str, count: int, title: str, legend: tuple, hidden: int, winner_label: str,
           winner_number: int, logo: bytes = b"") -> Image.Image:
    """الخلفية الثابتة: توهج، ظل، إطار معدني، قائمة المشاركين (يسار)، وبطاقة العنوان والحالة والفائز (يمين)."""
    bg = Image.new("RGBA", (CW, CH), BG + (255,))
    px = ImageDraw.Draw(bg)
    for y in range(CH):
        px.line((0, y, CW, y), fill=tuple(int(BG[i] + (24, 18, 44)[i] * (y / CH)) for i in range(3)) + (255,))
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((CX - R - 90, CY - R - 70, CX + R + 90, CY + R + 70), fill=BG_GLOW + (150,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(52)))
    shadow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((CX - R - 14, CY - R + 4, CX + R + 14, CY + R + 26), fill=(0, 0, 0, 170))
    bg.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))

    # الإطار المعدني (حلقة داكنة بحدّين ذهبيين وظل داخلي)
    ring = Image.new("RGBA", (CW * SS, CH * SS), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    c = (CX * SS, CY * SS)
    for rad, fill in ((R + 26, GOLD_D), (R + 24, GOLD_L), (R + 21, RIM), (R + 1, (14, 12, 26))):
        rd.ellipse((c[0] - rad * SS, c[1] - rad * SS, c[0] + rad * SS, c[1] + rad * SS), fill=fill + (255,))
    rd.ellipse((c[0] - (R + 8) * SS, c[1] - (R + 8) * SS, c[0] + (R + 8) * SS, c[1] + (R + 8) * SS),
               outline=(255, 255, 255, 26), width=SS)
    bg.alpha_composite(ring.resize((CW, CH), Image.LANCZOS))

    if not has_text_font():
        return bg
    text, col = STATUS_STYLE[status]
    side = 228

    def put(img_text: Image.Image, x: float, y: float, anchor: str = "m"):
        """يضع صورة نص: anchor = m (وسط) | r (الحافة اليمنى عند x) | l (اليسرى)."""
        left = x - img_text.width / 2 if anchor == "m" else x - img_text.width if anchor == "r" else x
        bg.alpha_composite(img_text, (int(left), int(y - img_text.height / 2)))

    # ---- اللوحة اليمنى: العنوان + عدد المشاركين + الحالة + (الفائز)
    rx0, rx1 = CW - 24 - side, CW - 24
    _pill(bg, (rx0, 22, rx1, 66), col + (46,), col + (190,))
    badge = _brand.circle(logo, 34) if logo else None
    if badge is not None:       # الشعار في موضع ثابت: يمين بطاقة العنوان
        ImageDraw.Draw(bg).ellipse((rx1 - 42, 25, rx1 - 4, 63), outline=col + (230,), width=2)
        bg.alpha_composite(badge, (rx1 - 40, 27))
    put(_text_image(title, 30, fill=WHITE, stroke=(0, 0, 0, 120)), (rx0 + rx1) / 2 - (18 if badge is not None else 0), 44)
    label = _count_label(count)
    _pill(bg, (rx0, 78, rx1, 112), (255, 255, 255, 24), (255, 255, 255, 70))
    put(_text_image(label, 20, fill=(214, 210, 236), stroke=(0, 0, 0, 90)), (rx0 + rx1) / 2, 95)
    _pill(bg, (rx0, 124, rx1, 158), col + (34,), col + (170,))
    ImageDraw.Draw(bg).ellipse((rx1 - 30, 135, rx1 - 16, 149), fill=col + (255,))
    put(_text_image(text, 20, fill=WHITE, stroke=(0, 0, 0, 100)), (rx0 + rx1) / 2 - 8, 141)
    if status == "result" and winner_label:
        _pill(bg, (rx0, 178, rx1, 330), (200, 140, 30, 60), GOLD_L + (230,), 2)
        d = ImageDraw.Draw(bg)
        ccx, ccy = (rx0 + rx1) / 2, 214
        pts = [(ccx - 26, ccy + 12), (ccx - 26, ccy - 14), (ccx - 13, ccy - 2), (ccx, ccy - 18), (ccx + 13, ccy - 2),
               (ccx + 26, ccy - 14), (ccx + 26, ccy + 12)]
        d.polygon(pts, fill=GOLD_L + (255,), outline=GOLD_D + (255,))
        for qx, qy in ((ccx - 26, ccy - 14), (ccx, ccy - 18), (ccx + 26, ccy - 14)):
            d.ellipse((qx - 3, qy - 3, qx + 3, qy + 3), fill=WHITE + (255,))
        size = 30
        t = _text_image(winner_label, size, fill=WHITE, stroke=(70, 40, 0, 220))
        while t.width > side - 28 and size > 14:
            size -= 2
            t = _text_image(winner_label, size, fill=WHITE, stroke=(70, 40, 0, 220))
        put(t, (rx0 + rx1) / 2, 262)
        if winner_number:
            put(_text_image(f"قطاع رقم {winner_number}", 17, fill=(255, 232, 160), stroke=(0, 0, 0, 90)),
                (rx0 + rx1) / 2, 304)

    # ---- اللوحة اليسرى: المشاركون بألوانهم
    lx0, lx1 = 24, 24 + side
    _pill_box = (lx0, 22, lx1, CH - 24)
    layer = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(_pill_box, radius=22, fill=(27, 24, 50, 170), outline=col + (120,), width=2)
    bg.alpha_composite(layer)
    put(_text_image("المشاركون", 22, fill=WHITE, stroke=(0, 0, 0, 100)), (lx0 + lx1) / 2, 50)
    d = ImageDraw.Draw(bg)
    y = 90
    for text_, color, _idx, is_win in legend:
        if is_win:
            _pill(bg, (lx0 + 10, y - 17, lx1 - 10, y + 17), GOLD + (60,), GOLD_L + (220,), 2)
        d.ellipse((lx1 - 32, y - 8, lx1 - 16, y + 8), fill=color + (255,), outline=(255, 255, 255, 90), width=1)
        size = 19
        t = _text_image(text_, size, fill=WHITE, stroke=(0, 0, 0, 120))
        while t.width > side - 70 and size > 12:
            size -= 1
            t = _text_image(text_, size, fill=WHITE, stroke=(0, 0, 0, 120))
        put(t, lx1 - 42, y, "r")
        y += 34
    if hidden > 0:
        put(_text_image(f"و {hidden} آخرين", 17, fill=(170, 166, 200), stroke=(0, 0, 0, 90)), (lx0 + lx1) / 2, y + 4)
    if not legend:
        put(_text_image("لا مشاركين بعد", 18, fill=(170, 166, 200), stroke=(0, 0, 0, 90)), (lx0 + lx1) / 2, 140)
    return bg


@lru_cache(maxsize=8)
def _bulbs(phase: int, all_on: bool) -> Image.Image:
    layer = Image.new("RGBA", (CW * SS, CH * SS), (0, 0, 0, 0))
    glow = Image.new("RGBA", (CW * SS, CH * SS), (0, 0, 0, 0))
    d, g = ImageDraw.Draw(layer), ImageDraw.Draw(glow)
    rr = (R + 14) * SS
    for i in range(BULBS):
        a = math.radians(-90 + i * 360 / BULBS)
        x, y = CX * SS + rr * math.cos(a), CY * SS + rr * math.sin(a)
        on = all_on or (i + phase) % 3 == 0
        col = (255, 232, 160) if on else (96, 84, 132)
        d.ellipse((x - 4 * SS, y - 4 * SS, x + 4 * SS, y + 4 * SS), fill=col + (255,))
        if on:
            g.ellipse((x - 11 * SS, y - 11 * SS, x + 11 * SS, y + 11 * SS), fill=(255, 205, 100, 150))
    out = glow.filter(ImageFilter.GaussianBlur(5 * SS))
    out.alpha_composite(layer)
    return out.resize((CW, CH), Image.LANCZOS)


@lru_cache(maxsize=1)
def _suspense_ring() -> Image.Image:
    """توهج ذهبي ناعم حول الإطار يُظهَر تدريجيًا عند اقتراب التوقف."""
    layer = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((CX - R - 18, CY - R - 18, CX + R + 18, CY + R + 18), outline=GOLD_L + (255,), width=10)
    return layer.filter(ImageFilter.GaussianBlur(9))


@lru_cache(maxsize=1)
def _hub() -> Image.Image:
    big = Image.new("RGBA", (CW * SS, CH * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    cx, cy = CX * SS, CY * SS
    for rad, fill in ((44, GOLD_D), (42, GOLD_L), (38, RIM), (31, (22, 18, 40)), (26, GOLD)):
        d.ellipse((cx - rad * SS, cy - rad * SS, cx + rad * SS, cy + rad * SS), fill=fill + (255,))
    for i in range(12):  # نقاط زخرفية على الحلقة
        a = math.radians(i * 30)
        x, y = cx + 35 * SS * math.cos(a), cy + 35 * SS * math.sin(a)
        d.ellipse((x - 2 * SS, y - 2 * SS, x + 2 * SS, y + 2 * SS), fill=GOLD_L + (255,))
    pts = []
    for i in range(10):
        rad = 20 * SS if i % 2 == 0 else 9 * SS
        a = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    d.polygon(pts, fill=(34, 26, 56, 255))
    d.ellipse((cx - 4 * SS, cy - 4 * SS, cx + 4 * SS, cy + 4 * SS), fill=GOLD_L + (255,))
    shadow = Image.new("RGBA", big.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((cx - 46 * SS, cy - 40 * SS, cx + 46 * SS, cy + 52 * SS), fill=(0, 0, 0, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(7 * SS))
    shadow.alpha_composite(big)
    return shadow.resize((CW, CH), Image.LANCZOS)


@lru_cache(maxsize=32)
def _pointer(tilt: int = 0) -> Image.Image:
    """المؤشر الذهبي الثابت. tilt = ميلان خفيف عند اصطدامه بفواصل القطاعات."""
    w, h = 54 * SS, 70 * SS
    p = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.polygon([(8 * SS, 8 * SS), (w - 4 * SS, 8 * SS), (w // 2 + 2 * SS, h - 2 * SS)], fill=(0, 0, 0, 120))
    d.polygon([(4 * SS, 2 * SS), (w - 4 * SS, 2 * SS), (w // 2, h - 8 * SS)], fill=GOLD_D + (255,))
    d.polygon([(7 * SS, 5 * SS), (w - 7 * SS, 5 * SS), (w // 2, h - 14 * SS)], fill=GOLD + (255,))
    d.polygon([(7 * SS, 5 * SS), (w // 2, 5 * SS), (w // 2, h - 14 * SS)], fill=GOLD_L + (255,))
    d.ellipse((w // 2 - 8 * SS, 11 * SS, w // 2 + 8 * SS, 27 * SS), fill=WHITE + (255,), outline=GOLD_D + (255,),
              width=2 * SS)
    p = p.resize((w // SS, h // SS), Image.LANCZOS)
    if tilt:
        p = p.rotate(tilt, resample=Image.BICUBIC, center=(p.width / 2, 8))
    return p


def _frame(status: str, count: int, title: str, wheel: Image.Image, angle: float, *, names: Sequence[str] = (),
           winner: Optional[int] = None, winner_label: str = "", phase: int = 0,
           all_on: bool = False, tilt: int = 0, blur: float = 0.0, tension: float = 0.0,
           glow: Optional[Image.Image] = None,
           wheel_b: Optional[Image.Image] = None, mix: float = 0.0) -> Image.Image:
    legend, hidden = _legend_for(names, winner if status == "result" else None)
    number = (winner + 1) if (status == "result" and winner is not None and len(names) > NAMED_MAX) else 0
    frame = _scene(status, count, title, legend, hidden, winner_label if status == "result" else "", number,
                   _brand.current() or b"").copy()
    base = wheel if wheel_b is None or mix <= 0 else Image.blend(wheel, wheel_b, mix)
    rot = base.rotate(-angle, resample=Image.BICUBIC)
    if blur > 12:  # طمس حركي خفيف جدًا عند السرعة العالية فقط (الأسماء غير مقروءة وقتها أصلًا)
        ghost = base.rotate(-(angle - blur * 0.4), resample=Image.BICUBIC)
        rot = Image.blend(rot, ghost, 0.3)
    if glow is not None:
        frame.alpha_composite(glow)   # خلف العجلة: يظهر حول الإطار فقط ولا يغطي الأسماء
    frame.alpha_composite(rot, (CX - R, CY - R))
    frame.alpha_composite(_bulbs(phase, all_on))
    if tension > 0.05:
        ring = _suspense_ring()
        a = ring.getchannel("A").point(lambda v: int(v * min(0.55, tension * 0.55)))
        ring = ring.copy()
        ring.putalpha(a)
        frame.alpha_composite(ring)
    frame.alpha_composite(_hub())
    ptr = _pointer(tilt)
    frame.alpha_composite(ptr, (CX - ptr.width // 2, CY - R - 34))
    return frame


def _winner_glow(n: int, winner: int, angle: float) -> Image.Image:
    seg = 360 / n
    layer = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    r = R + 30
    start = -90 + winner * seg + angle
    ImageDraw.Draw(layer).pieslice((CX - r, CY - r, CX + r, CY + r), start, start + seg,
                                   fill=_colors(n)[winner] + (200,))
    return layer.filter(ImageFilter.GaussianBlur(16))


# ---------------------------------------------------------------------- الواجهات العامة
@dataclass
class SpinResult:
    winner: int
    angle: float
    gif: bytes
    duration_ms: int
    spin_ms: int = 0     # مدة الحركة نفسها قبل إطار الإعلان الأخير (لتحديد وقت انتظار البوت)


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


TITLE = "الروليت"


def render_lobby(names: Sequence[str], title: Optional[str] = None) -> bytes:
    """العجلة الثابتة أثناء انتظار المشاركين (بدون مشاركين: قطاعات رمادية رمزية)."""
    n = len(names)
    wheel = _wheel(names) if n else _wheel(["؟"] * 8, muted=True)
    return _png(_frame("waiting", n, title or TITLE, wheel, 0, names=names, phase=0))


def render_spin(names: Sequence[str], winner: int, angle: float, frames: int = 60, frame_ms: int = 45,
                title: Optional[str] = None) -> SpinResult:
    """GIF الدوران: تسارع ناعم ثم تباطؤ طويل، طمس حركي، توهج توتر، ارتداد خفيف، ثم إبراز الفائز بانتقال تدريجي.

    ينتهي عند angle بالضبط (المحسوبة من الفائز)؛ ويرفض الرسم إن لم تطابق الزاوية الفائز.
    """
    n = len(names)
    if segment_at(angle, n) != winner:  # حماية: الأنيميشن لا يقرر الفائز أبدًا
        raise ValueError("angle does not match winner")
    title = title or TITLE
    plain = _wheel(names)
    lit = _wheel(names, dim_except=winner)
    wlabel = clean_name(names[winner], 40) or f"#{winner + 1}"
    curve = spin_curve(frames)
    seg = 360 / n
    images, durations = [], []
    prev = 0.0
    for i, p in enumerate(curve):
        a = angle * p
        speed = a - prev
        prev = a
        # المؤشر يرتجف بقدر مرور الفواصل (أخف عند البطء)، والمصابيح تتباطأ مع العجلة
        tilt = 0 if speed < 0.6 else int(min(12, 2 + speed / max(seg, 1) * 5)) * (1 if i % 2 else -1)
        tension = max(0.0, (p - 0.86) / 0.14) if p < 0.995 else 1.0
        slow = 1 if speed > 8 else 2 if speed > 3 else 4
        images.append(_frame("spinning", n, title, plain, a, names=names, phase=(i // slow) % 3, tilt=tilt,
                             blur=speed, tension=tension))
        durations.append(frame_ms if speed > 2.5 else frame_ms + 30)
    # ارتداد صغير عند التوقف دون الخروج من قطاع الفائز
    amp = min(2.2, seg * 0.09)
    for k, f in enumerate((1.0, -0.55, 0.25, 0.0)):
        images.append(_frame("spinning", n, title, plain, angle + amp * f, names=names, phase=k,
                             tilt=int(-5 * f), tension=1.0))
        durations.append(80)
    spin_ms = sum(durations)
    # انتقال ناعم: إعتام بقية القطاعات وإبراز الفائز وتوهجه تدريجيًا
    glow_full = _winner_glow(n, winner, angle)
    for k, mix in enumerate((0.0, 0.2, 0.45, 0.7, 0.9, 1.0)):
        g = glow_full.copy()
        g.putalpha(g.getchannel("A").point(lambda v, m=mix: int(v * m)))
        images.append(_frame("result", n, title, plain, angle, names=names, winner=winner, winner_label=wlabel,
                             all_on=mix > 0.5, glow=g if mix > 0 else None, wheel_b=lit, mix=mix,
                             tension=1.0 - mix))
        durations.append(90)
    images.append(_frame("result", n, title, lit, angle, names=names, winner=winner, winner_label=wlabel,
                         all_on=True, glow=glow_full))
    durations.append(HOLD_MS)

    # لوحة ألوان مشتركة من إطار أثناء الدوران + إطار النتيجة
    gw, gh = int(CW * GIF_SCALE), int(CH * GIF_SCALE)
    small = [im.convert("RGB").resize((gw, gh), Image.LANCZOS) for im in images]
    sheet = Image.new("RGB", (gw * 3, gh))
    for j, idx in enumerate((len(small) // 3, 2 * len(small) // 3, len(small) - 1)):
        sheet.paste(small[idx], (gw * j, 0))
    palette_src = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pal_frames = [im.quantize(palette=palette_src, dither=Image.Dither.NONE) for im in small]
    buf = io.BytesIO()
    pal_frames[0].save(buf, "GIF", save_all=True, append_images=pal_frames[1:], duration=durations, loop=0,
                       disposal=1, optimize=False)
    data = buf.getvalue()
    if len(data) > MAX_GIF_BYTES and frames > 48:  # كبير جدًا لرفع ديسكورد: أعد الرسم بإطارات أقل
        return render_spin(names, winner, angle, frames=max(48, int(frames * 0.7)), frame_ms=frame_ms + 10,
                           title=title)
    return SpinResult(winner=winner, angle=angle, gif=data, duration_ms=sum(durations) - HOLD_MS + 2500,
                      spin_ms=spin_ms)


def render_result(names: Sequence[str], winner: int, angle: float, banner: Optional[str] = None,
                  title: Optional[str] = None) -> bytes:
    """الصورة النهائية الثابتة: الفائز مضاء والباقي معتم، وبطاقة باسمه وتاج على اليمين."""
    n = len(names)
    label = banner if banner is not None else clean_name(names[winner], 40)
    img = _frame("result", n, title or TITLE, _wheel(names, dim_except=winner), angle, names=names, winner=winner,
                 winner_label=label or f"#{winner + 1}", all_on=True, glow=_winner_glow(n, winner, angle))
    return _png(img)

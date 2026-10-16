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

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

try:  # تشكيل الحروف العربية (Pillow على ويندوز غالبًا بدون raqm)
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # pragma: no cover - تعمل العجلة بالأرقام فقط
    arabic_reshaper = None
    get_display = None

# ---------------------------------------------------------------------- الهوية البصرية
SIZE = 440                 # أبعاد الصورة النهائية
SS = 2                     # تكبير الرسم لتنعيم الحواف
RADIUS = 178               # نصف قطر العجلة
CENTER = (SIZE // 2, SIZE // 2 + 10)
BG = (20, 17, 36)
BG_GLOW = (60, 40, 120)
RIM = (36, 30, 62)
GOLD = (245, 192, 74)
WHITE = (255, 255, 255)
SEGMENT_COLORS = [
    (124, 92, 255),   # بنفسجي
    (79, 163, 255),   # أزرق
    (43, 214, 123),   # أخضر
    (245, 192, 74),   # ذهبي
    (255, 138, 76),   # برتقالي
    (255, 92, 122),   # وردي
    (166, 107, 255),  # بنفسجي فاتح
    (34, 195, 201),   # فيروزي
]
BULBS = 24
MAX_NAMED = 16             # أكثر من هذا العدد = أرقام فقط على العجلة

FONT_CANDIDATES = [
    os.getenv("BOT_FONT_PATH", ""),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "font.ttf"),
    "C:/Windows/Fonts/tahomabd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Tahoma Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
]


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


def spin_curve(frames: int, accel: float = 0.14) -> list[float]:
    """منحنى تقدم 0→1: تسارع خطي قصير ثم تباطؤ تربيعي ناعم حتى التوقف."""
    samples = 400
    v = []
    for i in range(samples):
        t = i / (samples - 1)
        if t < accel:
            v.append(t / accel)
        else:
            x = (t - accel) / (1 - accel)
            v.append((1 - x) ** 2.2)
    cum, total = [0.0], 0.0
    for x in v[1:]:
        total += x
        cum.append(total)
    cum = [c / total for c in cum]
    return [cum[round(i * (samples - 1) / (frames - 1))] for i in range(frames)]


# ---------------------------------------------------------------------- النصوص
@lru_cache(maxsize=None)
def _font_path() -> Optional[str]:
    for path in FONT_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


@lru_cache(maxsize=64)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def has_text_font() -> bool:
    return _font_path() is not None and arabic_reshaper is not None


def clean_name(name: str, limit: int = 14) -> str:
    """يحذف الإيموجي والرموز التي لا يدعمها الخط، ويقص الاسم الطويل."""
    kept = "".join(c for c in name if unicodedata.category(c)[0] in "LNPMZ" and unicodedata.category(c) != "Cf")
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[: limit - 1].rstrip() + "…"
    return kept


def shape(text: str) -> str:
    if arabic_reshaper and any("\u0600" <= c <= "\u06ff" for c in text):
        return get_display(arabic_reshaper.reshape(text))
    return text


def _text_image(text: str, size: int, fill=WHITE, stroke=(0, 0, 0, 140)) -> Image.Image:
    font = _font(size)
    text = shape(text)
    sw = max(1, size // 9)
    bbox = font.getbbox(text, stroke_width=sw)
    w, h = bbox[2] - bbox[0] + 4, bbox[3] - bbox[1] + 4
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((2 - bbox[0], 2 - bbox[1]), text, font=font, fill=fill,
                             stroke_width=sw, stroke_fill=stroke)
    return img


# ---------------------------------------------------------------------- الرسم
def _colors(n: int) -> list[tuple[int, int, int]]:
    cols = [SEGMENT_COLORS[i % len(SEGMENT_COLORS)] for i in range(n)]
    if n > 1 and cols[-1] == cols[0]:  # لا يتشابه أول قطاع وآخره
        cols[-1] = SEGMENT_COLORS[(n // 2) % len(SEGMENT_COLORS)]
        if cols[-1] in (cols[0], cols[-2]):
            cols[-1] = SEGMENT_COLORS[3]
    return cols


def _labels(names: Sequence[str]) -> list[str]:
    n = len(names)
    if n > MAX_NAMED or not has_text_font():
        return [str(i + 1) for i in range(n)]
    limit = 14 if n <= 8 else 10
    return [clean_name(nm, limit) or str(i + 1) for i, nm in enumerate(names)]


def _wheel(names: Sequence[str], highlight: Optional[int] = None) -> Image.Image:
    """العجلة في وضعها الأصلي (غير مدوّرة) كصورة RGBA بحجم 2R."""
    n = max(1, len(names))
    r = RADIUS * SS
    d = 2 * r
    img = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    seg = 360 / n
    cols = _colors(n)
    for i in range(n):
        start, end = -90 + i * seg, -90 + (i + 1) * seg
        fill = cols[i]
        if highlight is not None and i != highlight:
            fill = tuple(int(c * 0.38) for c in fill)
        draw.pieslice((0, 0, d - 1, d - 1), start, end, fill=fill)
    if n > 1:  # فواصل القطاعات
        for i in range(n):
            a = math.radians(-90 + i * seg)
            draw.line((r, r, r + r * math.cos(a), r + r * math.sin(a)), fill=(255, 255, 255, 170), width=2 * SS)
    # ظل داخلي خفيف يعطي عمقًا
    outer = Image.new("L", (d, d), 0)
    ImageDraw.Draw(outer).ellipse((0, 0, d - 1, d - 1), fill=255)
    inner = Image.new("L", (d, d), 0)
    ImageDraw.Draw(inner).ellipse((int(d * 0.06), int(d * 0.06), int(d * 0.94), int(d * 0.94)), fill=255)
    ring = ImageChops.subtract(outer, inner.filter(ImageFilter.GaussianBlur(10 * SS))).point(lambda x: x * 80 // 255)
    black = Image.new("L", (d, d), 0)
    img.alpha_composite(Image.merge("RGBA", (black, black, black, ring)))

    # الأسماء
    labels = _labels(names) if names else ["؟"] * n
    if names:
        size = (22 if n <= 6 else 18 if n <= 10 else 15 if n <= MAX_NAMED else 13) * SS
        for i, label in enumerate(labels):
            mid = -90 + (i + 0.5) * seg
            t = _text_image(label, size, fill=WHITE if highlight in (None, i) else (200, 200, 210))
            max_w = int(r * 0.56)
            if t.width > max_w:
                t = t.resize((max_w, max(1, int(t.height * max_w / t.width))), Image.LANCZOS)
            rot = -mid
            if 90 < (mid % 360) < 270:  # لا تُعرض الكتابة مقلوبة في النصف الأيسر
                rot += 180
            t = t.rotate(rot, expand=True, resample=Image.BICUBIC)
            rad = math.radians(mid)
            cx = r + r * 0.6 * math.cos(rad)
            cy = r + r * 0.6 * math.sin(rad)
            img.alpha_composite(t, (int(cx - t.width / 2), int(cy - t.height / 2)))
    return img.resize((2 * RADIUS, 2 * RADIUS), Image.LANCZOS)


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    bg = Image.new("RGBA", (SIZE, SIZE), BG + (255,))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((CENTER[0] - RADIUS - 40, CENTER[1] - RADIUS - 40,
                                  CENTER[0] + RADIUS + 40, CENTER[1] + RADIUS + 40), fill=BG_GLOW + (150,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(38)))
    # الإطار الخارجي
    d = ImageDraw.Draw(bg)
    rr = RADIUS + 16
    d.ellipse((CENTER[0] - rr, CENTER[1] - rr, CENTER[0] + rr, CENTER[1] + rr), fill=RIM + (255,),
              outline=GOLD + (255,), width=3)
    return bg


def _bulbs(phase: int, all_on: bool = False) -> Image.Image:
    """أضواء الإطار: تتناوب أثناء الدوران، وتضيء كلها ذهبيًا عند الفوز."""
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    glow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d, g = ImageDraw.Draw(layer), ImageDraw.Draw(glow)
    rr = RADIUS + 8
    for i in range(BULBS):
        a = math.radians(-90 + i * 360 / BULBS)
        x, y = CENTER[0] + rr * math.cos(a), CENTER[1] + rr * math.sin(a)
        on = all_on or (i + phase) % 2 == 0
        col = (255, 226, 140) if on else (110, 96, 150)
        d.ellipse((x - 3.5, y - 3.5, x + 3.5, y + 3.5), fill=col + (255,))
        if on:
            g.ellipse((x - 8, y - 8, x + 8, y + 8), fill=(255, 210, 110, 150))
    out = glow.filter(ImageFilter.GaussianBlur(4))
    out.alpha_composite(layer)
    return out


@lru_cache(maxsize=1)
def _hub() -> Image.Image:
    layer = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx, cy, r = CENTER[0] * SS, CENTER[1] * SS, 30 * SS
    d.ellipse((cx - r - 4 * SS, cy - r - 4 * SS, cx + r + 4 * SS, cy + r + 4 * SS), fill=GOLD + (255,))
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=RIM + (255,))
    d.ellipse((cx - 9 * SS, cy - 9 * SS, cx + 9 * SS, cy + 9 * SS), fill=GOLD + (255,))
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


@lru_cache(maxsize=16)
def _pointer(tilt: int = 0) -> Image.Image:
    """المؤشر الذهبي أعلى العجلة. tilt = ميلان خفيف يحاكي اصطدامه بالفواصل."""
    w, h = 44 * SS, 52 * SS
    p = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(p)
    d.polygon([(4 * SS, 4 * SS), (w - 4 * SS, 4 * SS), (w // 2, h - 4 * SS)], fill=(0, 0, 0, 110))
    d.polygon([(2 * SS, 0), (w - 2 * SS, 0), (w // 2, h - 8 * SS)], fill=GOLD + (255,), outline=WHITE + (255,))
    d.ellipse((w // 2 - 6 * SS, 6 * SS, w // 2 + 6 * SS, 18 * SS), fill=WHITE + (255,))
    p = p.resize((w // SS, h // SS), Image.LANCZOS)
    if tilt:
        p = p.rotate(tilt, resample=Image.BICUBIC, center=(p.width / 2, 6))
    return p


def _compose(wheel: Image.Image, angle: float, phase: int, tilt: int = 0, all_on: bool = False,
             glow: Optional[Image.Image] = None, banner: Optional[str] = None,
             title: Optional[str] = None) -> Image.Image:
    frame = _background().copy()
    if glow is not None:
        frame.alpha_composite(glow)
    rotated = wheel.rotate(-angle, resample=Image.BICUBIC)
    frame.alpha_composite(rotated, (CENTER[0] - RADIUS, CENTER[1] - RADIUS))
    frame.alpha_composite(_bulbs(phase, all_on))
    frame.alpha_composite(_hub())
    ptr = _pointer(tilt)
    frame.alpha_composite(ptr, (CENTER[0] - ptr.width // 2, CENTER[1] - RADIUS - 28))
    if title and has_text_font():
        t = _text_image(title, 20, fill=(235, 230, 255))
        frame.alpha_composite(t, ((SIZE - t.width) // 2, 8))
    if banner and has_text_font():
        t = _text_image(banner, 22, fill=WHITE, stroke=(60, 40, 0, 200))
        pad = 14
        bw, bh = t.width + pad * 2, t.height + 12
        x0, y0 = (SIZE - bw) // 2, SIZE - bh - 6
        pill = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=bh // 2,
                                               fill=(200, 140, 30, 235), outline=GOLD + (255,), width=2)
        frame.alpha_composite(pill)
        frame.alpha_composite(t, (x0 + pad, y0 + 6))
    return frame


def _winner_glow(n: int, winner: int, angle: float) -> Image.Image:
    seg = 360 / n
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    r = RADIUS + 22
    box = (CENTER[0] - r, CENTER[1] - r, CENTER[0] + r, CENTER[1] + r)
    start = -90 + winner * seg + angle
    ImageDraw.Draw(layer).pieslice(box, start, start + seg, fill=_colors(n)[winner] + (230,))
    return layer.filter(ImageFilter.GaussianBlur(14))


# ---------------------------------------------------------------------- الواجهات العامة
@dataclass
class SpinResult:
    winner: int
    angle: float
    gif: bytes
    duration_ms: int


def _to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, fmt, optimize=True)
    return buf.getvalue()


def render_lobby(names: Sequence[str], title: Optional[str] = None) -> bytes:
    """العجلة الثابتة أثناء انتظار المشاركين (فارغة = قطاعات رمزية)."""
    if names:
        wheel = _wheel(names)
    else:
        wheel = _wheel(["؟"] * 8)
    return _to_bytes(_compose(wheel, 0, 0))


def render_spin(names: Sequence[str], winner: int, angle: float, frames: int = 64,
                frame_ms: int = 45) -> SpinResult:
    """GIF الدوران: يبدأ من 0 وينتهي بالضبط عند angle (المحسوبة من الفائز)."""
    n = len(names)
    if segment_at(angle, n) != winner:  # حماية: الأنيميشن لا يقرر الفائز أبدًا
        raise ValueError("angle does not match winner")
    wheel = _wheel(names)
    curve = spin_curve(frames)
    seg = 360 / n
    images, durations = [], []
    prev = 0.0
    for i, p in enumerate(curve):
        a = angle * p
        speed = a - prev
        prev = a
        # المؤشر يرتجف بقدر سرعة مرور الفواصل (أخف عند البطء)
        tilt = 0 if speed < 0.5 else int(min(14, 3 + speed / max(seg, 1) * 6)) * (1 if i % 2 else -1)
        images.append(_compose(wheel, a, phase=i // 2, tilt=tilt))
        durations.append(frame_ms if p < 0.9 else frame_ms + 25)
    # اهتزاز ارتدادي صغير عند التوقف، دون تجاوز حدود قطاع الفائز
    amp = min(2.5, seg * 0.1)
    for k, f in enumerate((1.0, -0.6, 0.3, 0.0)):
        images.append(_compose(wheel, angle + amp * f, phase=k, tilt=int(-6 * f)))
        durations.append(70)
    # إطار الإعلان مع التوهج
    final = _compose(_wheel(names, highlight=winner), angle, phase=0, all_on=True,
                     glow=_winner_glow(n, winner, angle))
    images.append(final)
    durations.append(2500)

    # لوحة ألوان مشتركة من إطار عادي + إطار الإعلان (المعتم) حتى لا تبهت الألوان أثناء الدوران
    sheet = Image.new("RGB", (SIZE * 2, SIZE))
    sheet.paste(images[len(images) // 2].convert("RGB"), (0, 0))
    sheet.paste(images[-1].convert("RGB"), (SIZE, 0))
    palette_src = sheet.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    pal_frames = [im.convert("RGB").quantize(palette=palette_src, dither=Image.Dither.NONE) for im in images]
    buf = io.BytesIO()
    pal_frames[0].save(buf, "GIF", save_all=True, append_images=pal_frames[1:], duration=durations,
                       disposal=1, optimize=False)
    return SpinResult(winner=winner, angle=angle, gif=buf.getvalue(), duration_ms=sum(durations))


def render_result(names: Sequence[str], winner: int, angle: float, banner: Optional[str] = None) -> bytes:
    """الصورة النهائية الثابتة: الفائز مضاء والباقي معتم، مع شريط باسمه."""
    n = len(names)
    label = banner if banner is not None else clean_name(names[winner], 22)
    img = _compose(_wheel(names, highlight=winner), angle, phase=0, all_on=True,
                   glow=_winner_glow(n, winner, angle), banner=label or None)
    return _to_bytes(img)

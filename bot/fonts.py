"""نظام الخطوط الموحّد لكل صور البوت: سلسلة خطوط واحدة، واختيار الخط لكل حرف، وبلا مربعات «tofu».

الهدف: خط على طراز DIN (أرقام وحروف لاتينية) + عربي متناسق، ونفس الخط في كل صورة (ألعاب، بطاقات، روليت، موسيقى).

ترتيب سلسلة الخطوط (أول خط يملك الحرف يُستخدم لرسمه):
 1) أي خطوط تضعها أنت في  assets/fonts/  (أو المجلد في BOT_FONT_DIR). ضع خط DIN المرخّص هناك (ملفات .ttf/.otf)
    ويُستخدم أولًا؛ يُفضَّل ملف يدعم العربية مثل «DIN Next Arabic». تسمية الوزن بالاسم: Bold/Black ← عريض،
    وغير ذلك ← متوسط. لا تُرفع الخطوط المرخّصة إلى GitHub (المجلد مستثنى في .gitignore).
 2) Bahnschrift (خط مايكروسوفت المبني على DIN 1451، يأتي مع ويندوز 10+): للأرقام والحروف اللاتينية.
 3) Dubai ثم Tahoma ثم Segoe UI: للعربية والرموز الناقصة (مثل × ÷ − وبعض أشكال الحروف العربية).
 4) خطوط لينكس/ماك الشائعة (Noto Sans Arabic، DejaVu، Liberation...) عند التشغيل على خادم غير ويندوز.

الخطوط الأربعة في (2) و(3) تأتي مع ويندوز ولا يعيد المشروع توزيعها. على خادم لينكس ضع خطوطك في assets/fonts
(خيارات مجانية بترخيص SIL OFL تناسب العربية: Tajawal أو IBM Plex Sans Arabic أو Noto Sans Arabic،
ولاتيني قريب من DIN: D-DIN).

فحص سريع للإعداد الحالي:  python -m bot.fonts
"""
from __future__ import annotations

import glob
import logging
import os
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from PIL import Image, ImageChops, ImageDraw, ImageFont

try:  # تشكيل الحروف العربية (Pillow على ويندوز غالبًا بدون raqm)
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # pragma: no cover
    arabic_reshaper = None
    get_display = None

log = logging.getLogger("bot.fonts")
PROJECT = Path(__file__).resolve().parent.parent
BOLD, MEDIUM = "bold", "medium"
_WIN = "C:/Windows/Fonts/"
_LINUX = ["/usr/share/fonts/truetype/noto/", "/usr/share/fonts/opentype/noto/", "/usr/share/fonts/truetype/dejavu/",
          "/usr/share/fonts/TTF/", "/usr/share/fonts/truetype/liberation/", "/usr/share/fonts/truetype/freefont/",
          "/usr/share/fonts/noto/", os.path.expanduser("~/.fonts/"), os.path.expanduser("~/.local/share/fonts/")]
_MAC = ["/System/Library/Fonts/Supplemental/", "/Library/Fonts/", os.path.expanduser("~/Library/Fonts/")]

# (ملف، اسم تنويعة للخطوط المتغيّرة) لكل وزن، بترتيب الأفضلية (بعد خطوط assets)
SYSTEM_CHAIN = {
    BOLD: [
        ("bahnschrift.ttf", "Bold"), ("DUBAI-BOLD.TTF", None), ("tahomabd.ttf", None), ("segoeuib.ttf", None),
        ("arialbd.ttf", None), ("NotoSansArabic-Bold.ttf", None), ("NotoNaskhArabic-Bold.ttf", None),
        ("DejaVuSans-Bold.ttf", None), ("LiberationSans-Bold.ttf", None), ("FreeSansBold.ttf", None),
        ("Tahoma Bold.ttf", None), ("Arial Bold.ttf", None), ("Arial Unicode.ttf", None),
    ],
    MEDIUM: [
        ("bahnschrift.ttf", "SemiBold"), ("DUBAI-MEDIUM.TTF", None), ("tahoma.ttf", None), ("segoeui.ttf", None),
        ("arial.ttf", None), ("NotoSansArabic-Regular.ttf", None), ("NotoNaskhArabic-Regular.ttf", None),
        ("DejaVuSans.ttf", None), ("LiberationSans-Regular.ttf", None), ("FreeSans.ttf", None),
        ("Tahoma.ttf", None), ("Arial.ttf", None), ("Arial Unicode.ttf", None),
    ],
}


@dataclass(frozen=True)
class Face:
    path: str
    variation: Optional[str] = None

    @property
    def name(self) -> str:
        return os.path.basename(self.path) + (f"[{self.variation}]" if self.variation else "")


def _font_dirs() -> list[str]:
    dirs = []
    if os.getenv("BOT_FONT_DIR"):
        dirs.append(os.getenv("BOT_FONT_DIR"))
    dirs.append(str(PROJECT / "assets" / "fonts"))
    return dirs


def _user_faces() -> dict[str, list[Face]]:
    """خطوط assets/fonts: أسماؤها تحدد الوزن، وما يحوي «din» يتقدّم على غيره."""
    out: dict[str, list[Face]] = {BOLD: [], MEDIUM: []}
    files: list[str] = []
    for d in _font_dirs():
        for ext in ("ttf", "otf", "ttc"):
            files += glob.glob(os.path.join(d, f"*.{ext}")) + glob.glob(os.path.join(d, f"*.{ext.upper()}"))
    files = sorted(set(files), key=lambda p: (0 if "din" in os.path.basename(p).lower() else 1, p.lower()))
    for f in files:
        low = os.path.basename(f).lower()
        is_bold = any(k in low for k in ("bold", "black", "heavy", "extrabold"))
        is_light = any(k in low for k in ("light", "thin"))
        if is_bold:
            out[BOLD].append(Face(f))
        elif not is_light:
            out[MEDIUM].append(Face(f))
            if not any(k in low for k in ("italic", "oblique")):
                out[BOLD].append(Face(f))   # غياب نسخة عريضة: نستخدم المتوفرة بدل الرجوع لخط آخر
    return out


def _system_faces(weight: str) -> list[Face]:
    dirs = [_WIN] + _LINUX + _MAC
    found = []
    for fname, var in SYSTEM_CHAIN[weight]:
        for d in dirs:
            p = os.path.join(d, fname)
            if os.path.isfile(p):
                found.append(Face(p, var))
                break
    return found


@lru_cache(maxsize=None)
def chain(weight: str = BOLD) -> tuple[Face, ...]:
    user = _user_faces()[weight]
    seen, out = set(), []
    for f in list(user) + _system_faces(weight):
        key = (f.path, f.variation)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return tuple(out)


@lru_cache(maxsize=256)
def load(face: Face, size: int) -> ImageFont.FreeTypeFont:
    # التشكيل وترتيب الاتجاه (bidi) نفّذناهما بأنفسنا في shape(): نفرض المحرك الأساسي حتى لا تعيد raqm ترتيب النص
    # على أجهزة تتوفر فيها (لينكس/ماك) فيظهر العربي معكوسًا ومفكّك الحروف.
    font = ImageFont.truetype(face.path, max(1, int(size)), layout_engine=ImageFont.Layout.BASIC)
    if face.variation:
        try:
            font.set_variation_by_name(face.variation)
        except (OSError, ValueError):
            pass
    return font


@lru_cache(maxsize=None)
def _notdef(face: Face) -> Image.Image:
    return _render_probe(load(face, 40), "\uFFFF")


def _render_probe(font: ImageFont.FreeTypeFont, ch: str) -> Image.Image:
    im = Image.new("L", (96, 96), 0)
    ImageDraw.Draw(im).text((10, 10), ch, font=font, fill=255)
    return im


@lru_cache(maxsize=65536)
def has_glyph(face: Face, ch: str) -> bool:
    """هل يملك الخط هذا الحرف فعلًا؟ (يقارن رسمه برسم حرف غير موجود؛ المتطابق = مربع .notdef)."""
    if ch in " \t\n\u00a0":
        return True
    if unicodedata.category(ch) in ("Mn", "Cf", "Cc"):   # تشكيل/محارف تنسيق: لا ترسم أصلًا
        return True
    font = load(face, 40)
    return ImageChops.difference(_render_probe(font, ch), _notdef(face)).getbbox() is not None


@lru_cache(maxsize=65536)
def face_for(ch: str, weight: str = BOLD) -> Optional[Face]:
    for face in chain(weight):
        if has_glyph(face, ch):
            return face
    return None


def reload() -> None:
    """يمسح المخزّنات المؤقتة لإعادة اكتشاف الخطوط (بعد إضافة ملفات إلى assets/fonts)."""
    for fn in (chain, load, _notdef, has_glyph, face_for):
        fn.cache_clear()


def available() -> bool:
    return bool(chain(BOLD)) and arabic_reshaper is not None


def fingerprint() -> str:
    """بصمة سلسلة الخطوط الحالية (للتأكد أن كل الصور تستخدم الإعداد نفسه)."""
    return "|".join(f"{w}:{','.join(f.name for f in chain(w))}" for w in (BOLD, MEDIUM))


# ---------------------------------------------------------------------- التشكيل والتخطيط
def has_arabic(text: str) -> bool:
    return any("\u0600" <= c <= "\u06ff" or "\u0750" <= c <= "\u077f" or "\ufb50" <= c <= "\ufefc" for c in text)


def shape(text: str) -> str:
    """نص جاهز للرسم يسارًا لليمين: تشكيل الحروف العربية ثم ترتيب الاتجاهات (bidi)."""
    if arabic_reshaper and has_arabic(text):
        return get_display(arabic_reshaper.reshape(text))
    return text


def layout(text: str, weight: str = BOLD) -> list[tuple[str, Face]]:
    """يقسّم النص المُشكَّل (بالترتيب البصري) إلى مقاطع، لكل مقطع خطه. الحروف التي لا يملكها أي خط تُحذف."""
    runs: list[tuple[str, Face]] = []
    for ch in shape(text):
        face = face_for(ch, weight)
        if face is None:
            continue                       # لا مربعات: رمز غير مدعوم (إيموجي مثلًا) يُحذف بدل أن يظهر ▯
        if runs and runs[-1][1] == face:
            runs[-1] = (runs[-1][0] + ch, face)
        else:
            runs.append((ch, face))
    return runs


def missing(text: str, weight: str = BOLD) -> list[str]:
    """الحروف التي لا يملكها أي خط في السلسلة (للتشخيص والاختبار)."""
    return sorted({ch for ch in shape(text) if face_for(ch, weight) is None})


def measure(text: str, size: float, weight: str = BOLD) -> float:
    return sum(load(face, round(size)).getlength(seg) for seg, face in layout(text, weight))


def _baseline_metrics(text: str, size: int, weight: str):
    """مقاييس الخط المرجعي: خط العربية إن وُجد عربي في النص وإلا الخط اللاتيني الأول."""
    ch = next((c for c in shape(text) if has_arabic(c)), None) if has_arabic(text) else None
    face = (face_for(ch, weight) if ch else None) or (chain(weight)[0] if chain(weight) else None)
    return load(face, size).getmetrics() if face else (size, 0)


def draw(d: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: float, fill, *, anchor: str = "mm",
         weight: str = BOLD, stroke_width: int = 0, stroke_fill=None) -> float:
    """يرسم نصًا متعدد الخطوط على خط أساس واحد. anchor: l/m/r أفقيًا و m/s عموديًا (middle/baseline)."""
    size = round(size)
    runs = layout(text, weight)
    if not runs:
        return 0.0
    widths = [load(face, size).getlength(seg) for seg, face in runs]
    total = sum(widths)
    x, y = xy
    h = anchor[0]
    x0 = x - total if h == "r" else x - total / 2 if h == "m" else x
    asc, desc = _baseline_metrics(text, size, weight)
    base = y + (asc - desc) / 2 if anchor[1] == "m" else y
    for (seg, face), w in zip(runs, widths):
        kw = {"stroke_width": stroke_width, "stroke_fill": stroke_fill} if stroke_width else {}
        d.text((x0, base), seg, font=load(face, size), fill=fill, anchor="ls", **kw)
        x0 += w
    return total


def render(text: str, size: float, fill=(255, 255, 255), stroke=(0, 0, 0, 140), weight: str = BOLD) -> Image.Image:
    """صورة RGBA شفافة للنص (تُدوَّر وتُلصق على العجلة)."""
    size = round(size)
    sw = max(1, size // 9)
    w = int(measure(text, size, weight)) + sw * 2 + 8
    asc, desc = _baseline_metrics(text, size, weight)
    h = asc + desc + sw * 2 + 8
    img = Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    draw(ImageDraw.Draw(img), (sw + 4, asc + sw + 4), text, size, fill, anchor="ls", weight=weight,
         stroke_width=sw, stroke_fill=stroke)
    return img


def report() -> str:
    lines = [f"BOLD  : {', '.join(f.name for f in chain(BOLD)) or '— لا شيء —'}",
             f"MEDIUM: {', '.join(f.name for f in chain(MEDIUM)) or '— لا شيء —'}",
             f"assets/fonts (خطوطك): {[f.name for f in _user_faces()[BOLD]] or 'لا توجد — يُستخدم Bahnschrift/Dubai/Tahoma'}"]
    probes = {"حروف لاتينية": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", "أرقام": "0123456789 ٠١٢٣٤٥٦٧٨٩",
              "رموز": "×÷−+=.,:;!?()-/%#·«»", "عربي": "ابتثجحخدذرزسشصضطظعغفقكلمنهوىيءأإآؤئة ًٌٍَُِّْ",
              "عربي مُشكَّل": "محمد عبدالله — الإنذار الثاني، نقطة ٧٥"}
    for k, v in probes.items():
        m = missing(v)
        lines.append(f"{k:14}: {'✔ كامل' if not m else '✘ ينقص ' + ''.join(m)}")
    return "\n".join(lines)


if __name__ == "__main__":   # python -m bot.fonts
    print(report())

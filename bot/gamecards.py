"""بطاقات الألعاب (16:9، 1280×720): شخصية بصرية لكل لعبة على الطبقة المشتركة bot/canvas.py.

التقنية: Pillow (رسم برمجي) → PNG للحالات الثابتة، و GIF للحركة القصيرة (شريط الوقت، قصاصات الفوز).
السبب: رسائل ديسكورد لا تدعم CSS/JS ولا انتقالات بين نسختين من الرسالة، والصورة المولّدة محليًا لا تحتاج خادم ويب.

حدود معروفة (وليست مخفية):
- مؤقت GIF يبدأ من لحظة تحميل العميل للصورة (قد يتأخر ثانية أو اثنتين عن العدّاد الحقيقي)، لذلك العدّاد الرسمي
  يبقى في الرسالة (<t:…:R>) وهو المرجع. الإطار الأخير يبقى 10 دقائق (أطول من أي لعبة) فلا يعيد التشغيل فعليًا.
- لا يوجد تحكم بتقليل الحركة داخل ديسكورد؛ لذلك يوجد إعداد game_animations يعطّل الـ GIF ويستخدم صورة ثابتة.
"""
from __future__ import annotations

import math
import random
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from . import canvas as cv
from .canvas import (AMBER, GOLD, GREEN, HOLD_MS, LH, LW, MAX_GIF_FRAMES, MUTED, PANEL, RED, WHITE, H, W,
                     Canvas, gif_bytes, mix, px, text_width)

__all__ = ["W", "H", "HOLD_MS", "MAX_GIF_FRAMES", "available", "render_math", "render_typing", "render_scramble",
           "render_quiz", "render_fast", "render_guess", "render_result"]

available = cv.available


def _timed(base_fn, theme: str, seconds: int, animate: bool) -> tuple[bytes, str]:
    """يبني (صورة, امتداد): GIF يفرغ فيه شريط الوقت، أو PNG ثابت بشريط ممتلئ."""
    base = base_fn()
    if not animate or seconds < 3:
        base.timer(1.0, seconds)
        return base.png(), "png"
    step = max(1, math.ceil(seconds / MAX_GIF_FRAMES))
    frames, durs = [], []
    for s in range(seconds, -1, -step):
        f = base.copy()
        f.timer(s / seconds, s)
        frames.append(f.img)
        durs.append(step * 1000)
    if seconds % step:   # ضمان إطار أخير عند الصفر
        f = base.copy()
        f.timer(0, 0)
        frames.append(f.img)
        durs.append(step * 1000)
    durs[-1] = HOLD_MS
    return gif_bytes(frames, durs), "gif"


def _panel(c: Canvas, box):
    c.rr(box, 22, fill=PANEL + (236,), outline=c.theme.accent + (175,), width=2)


# ---------------------------------------------------------------------- الحساب السريع
def render_math(expr: str, seconds: int, reward: Optional[str], level: str = "", event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    ops = {"+", "−", "×", "÷", "-"}
    items = expr.split() + ["=", "؟"]

    def base():
        c = Canvas("math")
        c.header(reward, event, level or None)
        _panel(c, (22, 54, LW - 22, 264))
        size, gap = 92, 24
        widths = [text_width(p, size) for p in items]
        while sum(widths) + gap * (len(items) - 1) > LW - 90 and size > 40:
            size -= 4
            widths = [text_width(p, size) for p in items]
        x = (LW - (sum(widths) + gap * (len(items) - 1))) / 2
        for p, w in zip(items, widths):   # ترتيب اليسار لليمين دائمًا للعمليات الحسابية
            col = c.theme.accent2 if p in ops else (GOLD if p in ("=", "؟") else WHITE)
            c.text(p, x + w / 2, 160, size, col)
            x += w + gap
        c.text("اكتب الناتج في الشات", LW / 2, 292, 18, MUTED, shadow=False)
        return c

    return _timed(base, "math", seconds, animate)


# ---------------------------------------------------------------------- أسرع كتابة
def render_typing(word: str, seconds: int, reward: Optional[str], event: bool = False,
                  animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("typing")
        c.header(reward, event, f"{len(word)} حرف")
        _panel(c, (22, 58, LW - 22, 256))
        c.text(word, LW / 2, 157, 96, WHITE, max_w=LW - 100)
        c.text("اكتبها كما هي — الصورة لا تُنسخ", LW / 2, 288, 18, MUTED, shadow=False)
        return c

    return _timed(base, "typing", seconds, animate)


# ---------------------------------------------------------------------- فكّك الكلمة
def render_scramble(letters: Sequence[str], seconds: int, reward: Optional[str], hint: Optional[str] = None,
                    event: bool = False, animate: bool = True) -> tuple[bytes, str]:
    arabic = any("؀" <= ch <= "ۿ" for ch in letters)

    def base():
        c = Canvas("scramble")
        t = c.theme
        c.header(reward, event, f"{len(letters)} حروف")
        n = len(letters)
        tile = min(76, (LW - 70) / n - 10)
        gap = 10
        x0 = (LW - (n * tile + (n - 1) * gap)) / 2
        for i, ch in enumerate(letters):
            idx = n - 1 - i if arabic else i   # العربية تُقرأ من اليمين
            x = x0 + idx * (tile + gap)
            col = mix(t.accent, t.accent2, i / max(1, n - 1))
            c.rr((x, 82, x + tile, 82 + tile), 14, fill=PANEL + (242,), outline=col + (255,), width=2.4)
            c.text(ch, x + tile / 2, 82 + tile / 2 + 2, tile * 0.62, WHITE)
        c.text("رتّب الحروف واكتب الكلمة", LW / 2, 82 + tile + 34, 19, MUTED, shadow=False)
        if hint:
            hw = text_width(hint, 17) + 34
            c.pill((LW / 2 - hw / 2, 250, LW / 2 + hw / 2, 288), GOLD + (40,), GOLD + (200,), 1.4)
            c.text(hint, LW / 2, 269, 17, (255, 232, 160), shadow=False)
        return c

    return _timed(base, "scramble", seconds, animate)


# ---------------------------------------------------------------------- سؤال سريع (أزرار)
def render_quiz(question: str, options: Sequence[str], seconds: int, reward: Optional[str], event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("quiz")
        t = c.theme
        c.header(reward, event)
        _panel(c, (20, 52, LW - 20, 160))
        size = 27
        lines = c.wrap(question, size, LW - 100, 3)
        while len(lines) > 2 and size > 19:
            size -= 2
            lines = c.wrap(question, size, LW - 100, 3)
        top = 106 - (len(lines) - 1) * (size + 6) / 2
        for i, line in enumerate(lines):
            c.text(line, LW / 2, top + i * (size + 8), size, WHITE, max_w=LW - 100)
        cols = [t.accent, GREEN, AMBER, RED]
        bw, bh, gx, gy = 292, 56, 14, 12
        for i, opt in enumerate(options[:4]):
            r, col = divmod(i, 2)
            x = (LW - 2 * bw - gx) / 2 + (1 - col) * (bw + gx)   # الخيار الأول في اليمين
            y = 172 + r * (bh + gy)
            c.rr((x, y, x + bw, y + bh), 14, fill=PANEL + (238,), outline=cols[i] + (225,), width=1.8)
            c.rr((x + bw - 46, y + 8, x + bw - 8, y + bh - 8), 10, fill=cols[i] + (255,))
            c.text("ABCD"[i], x + bw - 27, y + bh / 2, 22, (20, 18, 36), shadow=False)
            c.text(opt, x + bw - 58, y + bh / 2, 21, WHITE, anchor="rm", max_w=bw - 84, shadow=False)
        return c

    return _timed(base, "quiz", seconds, animate)


# ---------------------------------------------------------------------- اسرع (إجابة كتابية)
def render_fast(question: str, seconds: int, reward: Optional[str], hint: Optional[str] = None, event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("fast")
        c.header(reward, event, "اكتب الإجابة")
        _panel(c, (20, 54, LW - 20, 238))
        size = 34
        lines = c.wrap(question, size, LW - 90, 4)
        while len(lines) > 3 and size > 22:
            size -= 2
            lines = c.wrap(question, size, LW - 90, 4)
        top = 146 - (len(lines) - 1) * (size + 8) / 2
        for i, line in enumerate(lines):
            c.text(line, LW / 2, top + i * (size + 10), size, WHITE, max_w=LW - 90)
        c.text("أول إجابة صحيحة تكتبها في الشات تفوز", LW / 2, 258, 18, MUTED, shadow=False)
        if hint:
            hw = text_width(hint, 17) + 34
            c.pill((LW / 2 - hw / 2, 272, LW / 2 + hw / 2, 304), GOLD + (40,), GOLD + (200,), 1.4)
            c.text(hint, LW / 2, 288, 17, (255, 232, 160), shadow=False)
        return c

    return _timed(base, "fast", seconds, animate)


# ---------------------------------------------------------------------- خمّن الرقم
def render_guess(low: int, high: int, maximum: int, total_guesses: int, max_tries: int,
                 reward: Optional[str], event: bool = False, last: Optional[str] = None) -> tuple[bytes, str]:
    c = Canvas("guess")
    t = c.theme
    c.header(reward, event, f"من 1 إلى {maximum:,}")
    _panel(c, (22, 54, LW - 22, 188))
    c.text(f"{low:,}", LW / 2 - 130, 121, 78, t.accent2)
    c.text(f"{high:,}", LW / 2 + 130, 121, 78, t.accent)
    c.line((LW / 2 - 52, 121), (LW / 2 + 52, 121), MUTED + (255,), 3)       # سهم نطاق مرسوم (لا يعتمد على الخط)
    c.poly([(LW / 2 - 60, 121), (LW / 2 - 46, 112), (LW / 2 - 46, 130)], MUTED + (255,))
    c.poly([(LW / 2 + 60, 121), (LW / 2 + 46, 112), (LW / 2 + 46, 130)], MUTED + (255,))
    tx0, tx1, ty = 56, LW - 56, 222
    c.rr((tx0, ty - 8, tx1, ty + 8), 8, fill=(255, 255, 255, 34))
    span = max(1, maximum - 1)
    a = tx0 + (tx1 - tx0) * (low - 1) / span
    b = tx0 + (tx1 - tx0) * (high - 1) / span
    c.rr((a, ty - 8, max(b, a + 16), ty + 8), 8, fill=t.accent + (255,))
    c.text("1", tx0, ty + 24, 14, MUTED, shadow=False)
    c.text(f"{maximum:,}", tx1, ty + 24, 14, MUTED, shadow=False)
    c.text(f"{high - low + 1:,} رقم محتمل  ·  {total_guesses} تخمين", LW / 2, 270, 20, MUTED, shadow=False)
    if last:
        c.text(last, LW / 2, 304, 19, (255, 232, 160), shadow=False, max_w=LW - 90)
    return c.png(), "png"


# ---------------------------------------------------------------------- شاشة النتيجة
def _confetti(img: Image.Image, t: float, seed: int = 7):
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    palette = [GOLD, GREEN, (79, 163, 255), RED, (166, 107, 255)]
    for _ in range(56):
        x0, speed, size = rnd.randint(px(10), W - px(10)), rnd.uniform(0.7, 1.5), rnd.randint(px(4), px(8))
        y0, col = rnd.randint(-H, 0), rnd.choice(palette)
        sway = math.sin(t * 6 + x0) * px(8)
        y = y0 + (t * speed * (H + px(120))) + H * 0.55
        if px(8) < y < H - px(8):
            x = x0 + sway
            d.rectangle((x, y, x + size, y + size * 0.6), fill=col + (230,))


def render_result(game: str, headline: str, details: Sequence[tuple[str, str]], *, winner: bool,
                  avatar: Optional[bytes] = None, reward: Optional[str] = None, animate: bool = True,
                  event: bool = False) -> tuple[bytes, str]:
    """شاشة النتيجة: فائز (أفاتار + تفاصيل + قصاصات ورق) أو انتهاء الوقت (كشف الإجابة)."""
    c = Canvas(game)
    if not winner:
        c.img.alpha_composite(Image.new("RGBA", c.img.size, (10, 9, 20, 120)))
    c.header(reward, event, "النتيجة")
    c.rr((22, 54, LW - 22, 336), 24, fill=PANEL + (238,), outline=(GOLD if winner else MUTED) + (190,), width=2)
    cy = 128
    if winner:
        ax = LW - 22 - 30 - 56
        c.avatar(avatar, ax, cy, 104, GOLD, headline)
        c.text(headline, ax - 66, cy - 14, 44, GOLD, anchor="rm", max_w=ax - 130)
        c.text("فاز بالجولة", ax - 66, cy + 28, 20, MUTED, anchor="rm", shadow=False)
    else:
        c.text(headline, LW / 2, cy - 8, 48, (225, 222, 245), max_w=LW - 100)
        c.text("انتهى الوقت دون فائز", LW / 2, cy + 36, 20, MUTED, shadow=False)
    n = len(details)
    if n:
        cw = (LW - 84 - (n - 1) * 12) / n
        for i, (k, v) in enumerate(details):
            x = LW - 42 - (i + 1) * cw - i * 12
            c.stat((x, 218, x + cw, 316), k, v, WHITE, 30)
    if not (winner and animate):
        return c.png(), "png"
    frames, durs = [], []
    for i in range(14):
        f = c.copy()
        _confetti(f.img, i / 14)
        frames.append(f.img)
        durs.append(70)
    frames.append(c.img)
    durs.append(HOLD_MS)
    return gif_bytes(frames, durs), "gif"

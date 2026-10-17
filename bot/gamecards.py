"""بطاقات الألعاب (16:9، 1280×720) على نظام التصميم الموحّد في bot/canvas.py.

الإطار المشترك لكل لعبة مؤقتة: عنوان اللعبة + مؤقت mm:ss + شريط وقت يفرغ + شارة الجائزة، ثم محتوى اللعبة.

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
from .canvas import (BAD, GOLD, GOLD_D, GOLD_L, GOOD, HOLD_MS, INK, INK2, INK3, LW, M, MAX_GIF_FRAMES, WARN, WHITE, H,
                     W, Canvas, gif_bytes, mix, px, text_width)
from .fonts import MEDIUM

__all__ = ["W", "H", "HOLD_MS", "MAX_GIF_FRAMES", "available", "render_math", "render_typing", "render_scramble",
           "render_quiz", "render_fast", "render_guess", "render_result"]

available = cv.available
SUBTLE = (176, 190, 226)      # نص ثانوي فوق بطاقة البطل الداكنة


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


def _hint_chip(c: Canvas, hint: str, y: float):
    c.chip(hint, LW / 2, y, "c", GOLD, size=16, h=34)


# ---------------------------------------------------------------------- الحساب السريع
def render_math(expr: str, seconds: int, reward: Optional[str], level: str = "", event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    ops = {"+", "−", "×", "÷", "-"}
    items = expr.split() + ["=", "؟"]

    def base():
        c = Canvas("math")
        c.game_frame(None, reward, f"المستوى: {level}" if level else None, event)
        c.card((M, 100, LW - M, 270), 24)
        size, gap = 92, 26
        widths = [text_width(p, size) for p in items]
        while sum(widths) + gap * (len(items) - 1) > LW - 110 and size > 40:
            size -= 4
            widths = [text_width(p, size) for p in items]
        x = (LW - (sum(widths) + gap * (len(items) - 1))) / 2
        for p, w in zip(items, widths):   # ترتيب اليسار لليمين دائمًا للعمليات الحسابية
            col = c.accent if p in ops else (INK3 if p == "=" else (GOLD_D if p == "؟" else INK))
            c.text(p, x + w / 2, 188, size, col)
            x += w + gap
        c.text("اكتب الناتج في الشات", LW / 2, 304, 18, INK2)
        return c

    return _timed(base, "math", seconds, animate)


# ---------------------------------------------------------------------- أسرع كتابة
def render_typing(word: str, seconds: int, reward: Optional[str], event: bool = False,
                  animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("typing")
        c.game_frame(None, reward, f"{len(word)} حرف", event)
        c.card((M, 100, LW - M, 270), 24)
        c.text(word, LW / 2, 186, 96, INK, max_w=LW - 110)
        c.text("اكتبها كما هي — الصورة لا تُنسخ", LW / 2, 304, 18, INK2)
        return c

    return _timed(base, "typing", seconds, animate)


# ---------------------------------------------------------------------- فكّك الكلمة
def render_scramble(letters: Sequence[str], seconds: int, reward: Optional[str], hint: Optional[str] = None,
                    event: bool = False, animate: bool = True) -> tuple[bytes, str]:
    arabic = any("؀" <= ch <= "ۿ" for ch in letters)

    def base():
        c = Canvas("scramble")
        t = c.theme
        c.game_frame(None, reward, f"{len(letters)} حروف", event)
        n = len(letters)
        tile = min(78, (LW - 2 * M - 20) / n - 10)
        gap = 10
        x0 = (LW - (n * tile + (n - 1) * gap)) / 2
        y0 = 110
        for i, ch in enumerate(letters):
            idx = n - 1 - i if arabic else i   # العربية تُقرأ من اليمين
            x = x0 + idx * (tile + gap)
            col = mix(t.accent, t.accent2, i / max(1, n - 1))
            c.card((x, y0, x + tile, y0 + tile), 16, outline=col, width=2.2)
            c.text(ch, x + tile / 2, y0 + tile / 2 + 2, tile * 0.6, INK)
        c.text("رتّب الحروف واكتب الكلمة", LW / 2, y0 + tile + 34, 19, INK2)
        if hint:
            _hint_chip(c, hint, 292)
        return c

    return _timed(base, "scramble", seconds, animate)


# ---------------------------------------------------------------------- سؤال سريع (أزرار)
def render_quiz(question: str, options: Sequence[str], seconds: int, reward: Optional[str], event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("quiz")
        t = c.theme
        c.game_frame(None, reward, "اختر الإجابة الصحيحة", event)
        size = 30
        lines = c.wrap(question, size, LW - 90, 3)
        while len(lines) > 2 and size > 20:
            size -= 2
            lines = c.wrap(question, size, LW - 90, 3)
        top = 136 - (len(lines) - 1) * (size + 10) / 2
        for i, line in enumerate(lines):
            c.text(line, LW / 2, top + i * (size + 12), size, INK, max_w=LW - 90)
        cols = [t.accent, GOOD, WARN, BAD]
        bw, bh, gx, gy = 292, 58, 16, 14
        for i, opt in enumerate(options[:4]):
            r, col = divmod(i, 2)
            x = (LW - 2 * bw - gx) / 2 + (1 - col) * (bw + gx)   # الخيار الأول في اليمين
            y = 204 + r * (bh + gy)
            c.card((x, y, x + bw, y + bh), bh / 2, outline=INK, width=1.8)
            c.circle(x + bw - 29, y + bh / 2, 15, cols[i] + (255,))
            c.text("ABCD"[i], x + bw - 29, y + bh / 2, 17, WHITE)
            lines2 = c.wrap(opt, 18, bw - 92, 2)
            if len(lines2) == 1:
                c.text(lines2[0], x + bw - 54, y + bh / 2, 19, INK, anchor="rm", max_w=bw - 80)
            else:
                for j, ln in enumerate(lines2):
                    c.text(ln, x + bw - 54, y + bh / 2 + (j - 0.5) * 22, 16.5, INK, anchor="rm", max_w=bw - 80)
        return c

    return _timed(base, "quiz", seconds, animate)


# ---------------------------------------------------------------------- اسرع (إجابة كتابية)
def render_fast(question: str, seconds: int, reward: Optional[str], hint: Optional[str] = None, event: bool = False,
                animate: bool = True) -> tuple[bytes, str]:
    def base():
        c = Canvas("fast")
        c.game_frame(None, reward, "اكتب الإجابة", event)
        c.card((M, 100, LW - M, 246), 24)
        size = 34
        lines = c.wrap(question, size, LW - 100, 4)
        while len(lines) > 3 and size > 22:
            size -= 2
            lines = c.wrap(question, size, LW - 100, 4)
        top = 173 - (len(lines) - 1) * (size + 10) / 2
        for i, line in enumerate(lines):
            c.text(line, LW / 2, top + i * (size + 12), size, INK, max_w=LW - 100)
        c.text("أول إجابة صحيحة تكتبها في الشات تفوز", LW / 2, 276, 18, INK2)
        if hint:
            _hint_chip(c, hint, 316)
        return c

    return _timed(base, "fast", seconds, animate)


# ---------------------------------------------------------------------- خمّن الرقم
def render_guess(low: int, high: int, maximum: int, total_guesses: int, max_tries: int,
                 reward: Optional[str], event: bool = False, last: Optional[str] = None) -> tuple[bytes, str]:
    c = Canvas("guess")
    t = c.theme
    c.header(None, f"من 1 إلى {maximum:,}", f"{total_guesses} تخمين", reward, event)
    c.hero((M, 76, LW - M, 200), 24)
    size = 66
    while max(text_width(f"{low:,}", size), text_width(f"{high:,}", size)) > 190 and size > 34:
        size -= 4
    c.text(f"{low:,}", LW / 2 - 135, 140, size, WHITE)
    c.text(f"{high:,}", LW / 2 + 135, 140, size, GOLD_L)
    c.line((LW / 2 - 40, 140), (LW / 2 + 40, 140), (255, 255, 255, 110), 3)
    c.poly([(LW / 2 - 50, 140), (LW / 2 - 36, 131), (LW / 2 - 36, 149)], (255, 255, 255, 110))
    c.poly([(LW / 2 + 50, 140), (LW / 2 + 36, 131), (LW / 2 + 36, 149)], (255, 255, 255, 110))
    c.text("النطاق الحالي", LW / 2, 96, 12.5, SUBTLE, weight=MEDIUM)
    tx0, tx1, ty = 44, LW - 44, 232
    c.rr((tx0, ty - 7, tx1, ty + 7), 7, fill=(213, 221, 237, 255))
    span = max(1, maximum - 1)
    a = tx0 + (tx1 - tx0) * (low - 1) / span
    b = tx0 + (tx1 - tx0) * (high - 1) / span
    c.rr((a, ty - 7, max(b, a + 14), ty + 7), 7, fill=tuple(t.accent) + (255,))
    c.text("1", tx0, ty + 22, 13, INK3, anchor="lm")
    c.text(f"{maximum:,}", tx1, ty + 22, 13, INK3, anchor="rm")
    c.text(f"{high - low + 1:,} رقم محتمل", LW / 2, 280, 20, INK2)
    if last:
        c.chip(last, LW / 2, 320, "c", GOLD, size=16, h=34)
    return c.png(), "png"


# ---------------------------------------------------------------------- شاشة النتيجة
def _confetti(img: Image.Image, t: float, seed: int = 7):
    rnd = random.Random(seed)
    d = ImageDraw.Draw(img)
    palette = [GOLD, GOOD, (47, 128, 237), BAD, (124, 82, 240)]
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
    """شاشة النتيجة: فائز (بطاقة بطل + أفاتار + تفاصيل + قصاصات ورق) أو انتهاء الوقت (كشف الإجابة)."""
    c = Canvas(game)
    c.header(None, "النتيجة", None, reward, event)
    if winner:
        c.hero((M, 76, LW - M, 204), 26, accent=GOLD)
        ax = LW - M - 30 - 48
        c.avatar(avatar, ax, 140, 92, GOLD, headline)
        c.text("الفائز بالجولة", ax - 66, 112, 14, SUBTLE, anchor="rm", weight=MEDIUM)
        c.text(headline, ax - 66, 148, 40, GOLD_L, anchor="rm", max_w=ax - 130)
        c.chip("مبروك", M + 34, 140, "l", GOLD, solid=True, size=15, h=30)
    else:
        c.card((M, 76, LW - M, 204), 26)
        c.text("الإجابة الصحيحة", LW / 2, 108, 14, INK3, weight=MEDIUM)
        c.text(headline, LW / 2, 152, 50, INK, max_w=LW - 110)
        c.chip("انتهى الوقت دون فائز", LW / 2, 188, "c", INK2, size=13, h=24)
    n = len(details)
    if n:
        cw = (LW - 2 * M - (n - 1) * 12) / n
        for i, (k, v) in enumerate(details):
            x = LW - M - (i + 1) * cw - i * 12
            c.tile((x, 222, x + cw, 330), k, v, INK, 28, raised=True)
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

"""قوالب البطاقات (إنفوجرافيك) على نظام التصميم الموحّد bot/canvas.py — كلها 16:9 بدقة 1280×720.

كل قالب دالة نقية: تستقبل بيانات حقيقية جاهزة من النظام (أرصدة، ترتيب، منتجات...) وتُرجع bytes لـ PNG.
تغيير الألوان/الخلفيات يتم في canvas.py مرة واحدة. لا يُولَّد شيء بالذكاء الاصطناعي: رسم برمجي سريع (~50ms).

قاعدة التسلسل البصري: بطاقة بطل داكنة واحدة للعنصر الأهم (الرصيد/الفنان/اللاعب)، وبطاقات بيضاء للباقي،
ونص رئيسي كحلي، وأرقام بخط عريض كبير، وتعليقات صغيرة خافتة.
"""
from __future__ import annotations

import io
import math
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from . import canvas as cv
from .canvas import (BAD, GOLD, GOLD_D, GOLD_L, GOOD, INK, INK2, INK3, LH, LINE, LW, M, SUNK, SURFACE, WARN, WHITE,
                     Canvas, mix, px, text_width)
from .fonts import MEDIUM
from .wheel import clean_name

available = cv.available
SUBTLE = (176, 190, 226)           # نص ثانوي فوق بطاقة البطل الداكنة
GLASS = (255, 255, 255, 26)        # بلاطة زجاجية فوق بطاقة البطل


def name_of(name: str, limit: int = 24) -> str:
    return clean_name(name, limit) or "عضو"


def _num(n: int) -> str:
    return f"{n:,}"


def _compact(n: int) -> str:
    """صيغة مختصرة للأرقام الضخمة: 123.5 مليار."""
    for div, word in ((10 ** 12, "تريليون"), (10 ** 9, "مليار"), (10 ** 6, "مليون"), (10 ** 3, "ألف")):
        if abs(n) >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + f" {word}"
    return str(n)


def _fit_num(n: int, size: float, max_w: float, min_size: float = 30) -> tuple[str, float]:
    """رقم كامل بأكبر خط يتسع، وإن لم يتسع حتى الحد الأدنى يُعرض مختصرًا (لا يُقتطع رقم أبدًا)."""
    full = _num(n)
    s = size
    while s > min_size and text_width(full, s) > max_w:
        s -= 2
    if text_width(full, s) <= max_w:
        return full, s
    short = _compact(n)
    s = size
    while s > 24 and text_width(short, s) > max_w:
        s -= 2
    return short, s


def _cover(data: bytes, w: int, h: int) -> Optional[Image.Image]:
    """يملأ المستطيل w×h بالصورة (قصّ مركزي بلا تشويه الأبعاد)."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    r = max(w / im.width, h / im.height)
    im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


def _paste_rounded(c: Canvas, im: Image.Image, box, radius: float):
    x0, y0, x1, y1 = box
    w, h = px(x1 - x0), px(y1 - y0)
    mask = Image.new("L", (w * 3, h * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * 3 - 1, h * 3 - 1), radius=px(radius) * 3, fill=255)
    layer = im.copy()
    layer.putalpha(mask.resize((w, h), Image.LANCZOS))
    c.img.alpha_composite(layer, (px(x0), px(y0)))


def _gift(c: Canvas, cx: float, cy: float, s: float, col):
    c.rr((cx - s, cy - s * 0.55, cx + s, cy + s * 0.9), 5, fill=tuple(col) + (255,))
    c.rr((cx - s * 1.1, cy - s * 0.95, cx + s * 1.1, cy - s * 0.4), 5, fill=mix(col, WHITE, 0.3) + (255,))
    c.rr((cx - s * 0.16, cy - s * 0.95, cx + s * 0.16, cy + s * 0.9), 2, fill=WHITE + (235,))
    c.circle(cx - s * 0.32, cy - s * 1.1, s * 0.28, None, tuple(col) + (255,), 2)
    c.circle(cx + s * 0.32, cy - s * 1.1, s * 0.28, None, tuple(col) + (255,), 2)


def _star(c: Canvas, cx: float, cy: float, r: float, col):
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    c.poly(pts, tuple(col) + (255,))


# ---------------------------------------------------------------------- المحفظة
def render_balance(*, name: str, avatar: Optional[bytes], currency: str, balance: int, rank: int, total_earned: int,
                   games_won: int, earned_today: int, daily_cap: int, guild_name: str) -> bytes:
    c = Canvas("balance")
    c.header("المحفظة", name_of(guild_name, 28))
    c.hero((M, 66, LW - M, 214), 26, accent=GOLD)
    ax = LW - M - 28 - 44
    c.avatar(avatar, ax, 140, 84, GOLD, name)
    nx = ax - 62
    c.text(name_of(name, 26), nx, 98, 22, WHITE, anchor="rm", max_w=nx - M - 150)
    c.text("الرصيد الحالي", nx, 122, 13.5, SUBTLE, anchor="rm", weight=MEDIUM)
    big, size = _fit_num(balance, 68, nx - M - 56 - 20)
    c.text(big, nx, 168, size, GOLD_L, anchor="rm")
    c.coin(M + 34, 200, 12)
    c.text(currency, M + 54, 200, 15, SUBTLE, anchor="lm", max_w=120, weight=MEDIUM)
    stats = [("الترتيب", f"#{_num(rank)}"), ("ألعاب فاز بها", _num(games_won)), ("إجمالي المكتسب", total_earned)]
    w = (LW - 2 * M - 2 * GAPX) / 3
    for i, (k, v) in enumerate(stats):
        x1 = LW - M - i * (w + GAPX)
        v = v if isinstance(v, str) else _fit_num(v, 26, w - 20, 17)[0]
        c.card((x1 - w, 228, x1, 288), 16, shadow=True)
        c.text(k, x1 - w / 2, 246, 12.5, INK3, max_w=w - 14, weight=MEDIUM)
        c.text(v, x1 - w / 2, 270, 24, INK, max_w=w - 18)
    if daily_cap:
        frac = min(1.0, earned_today / daily_cap)
        c.text("كسب اليوم من الألعاب", LW - M, 306, 12.5, INK2, anchor="rm", weight=MEDIUM)
        c.text(f"{_num(earned_today)} / {_num(daily_cap)}", M, 306, 12.5, INK2, anchor="lm", weight=MEDIUM)
        c.progress((M, 316, LW - M, 326), frac, GOLD if frac < 0.9 else WARN)
    return c.png()


GAPX = 12


# ---------------------------------------------------------------------- الملف الشخصي
def render_profile(*, name: str, tag: str, avatar: Optional[bytes], balance: int, currency: str, rank: int,
                   games_won: int, total_earned: int, warnings: int, roles: int, joined: str, created: str,
                   top_role: str, guild_name: str) -> bytes:
    c = Canvas("profile")
    c.header("الملف الشخصي", name_of(guild_name, 28), name_of(top_role, 16) if top_role else None)
    c.hero((M, 66, LW - M, 176), 26)
    ax = LW - M - 28 - 40
    c.avatar(avatar, ax, 121, 76, c.accent, name)
    nx = ax - 56
    c.text(name_of(name, 26), nx, 104, 28, WHITE, anchor="rm", max_w=330)
    c.text(tag, nx, 134, 14.5, SUBTLE, anchor="rm", max_w=330, weight=MEDIUM)
    big, size = _fit_num(balance, 36, 170, 22)
    c.coin(M + 28, 108, 14)
    c.text(big, M + 52, 108, size, GOLD_L, anchor="lm")
    c.text(currency, M + 52, 140, 13.5, SUBTLE, anchor="lm", max_w=150, weight=MEDIUM)
    items = [("الترتيب", f"#{_num(rank)}", INK), ("فوز بالألعاب", _num(games_won), INK),
             ("إجمالي المكتسب", _num(total_earned), INK), ("التحذيرات", _num(warnings), BAD if warnings else INK),
             ("الرتب", _num(roles), INK)]
    cols, gap = 5, 10
    w = (LW - 2 * M - (cols - 1) * gap) / cols
    for i, (k, v, col) in enumerate(items):
        x1 = LW - M - i * (w + gap)
        c.card((x1 - w, 190, x1, 252), 16)
        c.text(k, x1 - w / 2, 208, 12, INK3, max_w=w - 12, weight=MEDIUM)
        c.text(v, x1 - w / 2, 233, 22, col, max_w=w - 14)
    c.card((M, 266, LW - M, 332), 20)
    half = (LW - 2 * M) / 2
    for i, (k, v) in enumerate((("انضم للسيرفر", joined), ("إنشاء الحساب", created))):
        cx = LW - M - half * i - half / 2
        c.text(k, cx, 288, 12.5, INK3, weight=MEDIUM)
        c.text(v, cx, 313, 19, INK, max_w=half - 30)
    c.line((LW / 2, 280), (LW / 2, 318), LINE + (255,), 1.2)
    return c.png()


# ---------------------------------------------------------------------- المتصدرون
MEDALS = {1: ((244, 184, 40), WHITE), 2: ((165, 178, 202), WHITE), 3: ((205, 127, 70), WHITE)}


def render_leaderboard(*, rows: Sequence[dict], avatars: dict[int, Optional[bytes]], viewer_id: int,
                       viewer_rank: int, viewer_balance: int, currency: str, guild_name: str, page: int,
                       pages: int) -> bytes:
    """rows: [{rank, user_id, name, balance}] حتى 10 صفوف."""
    c = Canvas("board")
    c.header("لوحة المتصدرين", name_of(guild_name, 28), f"صفحة {page} من {pages}")
    single = len(rows) <= 5                    # قلة الصفوف: عمود واحد بعرض كامل بدل نصف الصورة فارغًا
    col_w, row_h, gap, top = ((LW - 2 * M) if single else (LW - 2 * M - 14) / 2), 44, 5, 66
    for i, r in enumerate(rows[:10]):
        col, row = divmod(i, 5)
        x1 = LW - M - col * (col_w + 14)
        x0, y0 = x1 - col_w, top + row * (row_h + gap)
        me = r["user_id"] == viewer_id
        medal = MEDALS.get(r["rank"])
        c.card((x0, y0, x1, y0 + row_h), 15, fill=(255, 247, 224) if me else SURFACE,
               outline=(GOLD if me else LINE), width=1.5 if me else 1)
        cy = y0 + row_h / 2
        bx = x1 - 24
        if medal:
            c.circle(bx, cy, 14, medal[0] + (255,))
            c.text(str(r["rank"]), bx, cy, 16, medal[1])
        else:
            c.text(f"{r['rank']}", bx, cy, 16, INK3)
        c.avatar(avatars.get(r["user_id"]), x1 - 70, cy, 28, medal[0] if medal else LINE, r["name"])
        tx = x1 - 96
        left_pad = 62 if me else 14
        c.text(name_of(r["name"], 22), tx, y0 + 15, 15.5, INK, anchor="rm", max_w=col_w - 112 - left_pad + 14)
        num, nsize = _fit_num(r["balance"], 17, col_w - 150 - left_pad + 14, 12)
        c.text(num, tx - 18, y0 + 32, nsize, GOLD_D, anchor="rm")
        c.coin(tx - 7, y0 + 32, 6.5)
        if me:
            c.chip("أنت", x0 + 12, cy, "l", GOLD, solid=True, size=12.5, h=22)
    if not rows:
        c.text("لا أحد في الترتيب بعد — العب واجمع النقاط!", LW / 2, 190, 22, INK2)
    c.hero((M, 318, LW - M, 346), 14, accent=GOLD)
    c.text(f"ترتيبك  #{_num(viewer_rank)}", LW - M - 16, 332, 14.5, WHITE, anchor="rm")
    vnum, vsize = _fit_num(viewer_balance, 15, 260, 11)
    c.text(f"{vnum}  {currency}", M + 16, 332, vsize, GOLD_L, anchor="lm")
    return c.png()


# ---------------------------------------------------------------------- المتجر
def render_store(*, items: Sequence[dict], images: dict[int, Optional[bytes]], balance: int, currency: str,
                 guild_name: str, page: int, pages: int) -> bytes:
    """items: [{id, name, price, kind, stock, duration_hours}] حتى 6 منتجات."""
    c = Canvas("shop")
    t = c.theme
    c.header("المتجر", name_of(guild_name, 28), f"صفحة {page} من {pages}", f"رصيدك {_compact(balance) if balance >= 10 ** 9 else _num(balance)}")
    cols, rows = 3, 2
    gx, gy = 12, 12
    tw = (LW - 2 * M - (cols - 1) * gx) / cols
    top, bottom = 66, 322
    th = (bottom - top - (rows - 1) * gy) / rows
    for i, it in enumerate(items[:6]):
        r, col = divmod(i, cols)
        x1 = LW - M - col * (tw + gx)
        x0, y0 = x1 - tw, top + r * (th + gy)
        sold = it["stock"] == 0
        affordable = balance >= it["price"]
        c.card((x0, y0, x1, y0 + th), 18, outline=LINE)
        ih = th * 0.36
        box = (x0 + 8, y0 + 8, x1 - 8, y0 + 8 + ih)
        data = images.get(it["id"])
        im = _cover(data, px(box[2] - box[0]), px(box[3] - box[1])) if data else None
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        if im is not None:
            _paste_rounded(c, im, box, 12)
        else:
            c.rr(box, 12, fill=mix(SURFACE, t.accent, 0.14) + (255,))
            if it["kind"] == "role":
                _star(c, cx, cy, 17, GOLD)
            else:
                _gift(c, cx, cy + 2, 13, t.accent)
        c.chip(f"#{it['id']}", x0 + 16, y0 + 20, "l", INK2, solid=True, size=10.5, h=16)
        if sold:
            c.rr(box, 12, fill=(255, 255, 255, 190))
            c.chip("نفد", cx, cy, "c", BAD, solid=True, size=14, h=24)
        c.text(name_of(it["name"], 60), x1 - 12, y0 + ih + 22, 14.5, INK if not sold else INK3, anchor="rm", max_w=tw - 24,
               min_scale=0.8)
        tags = []
        if it["duration_hours"]:
            h = it["duration_hours"]
            tags.append(f"{h // 24} يوم" if h % 24 == 0 else f"{h} ساعة")
        if it["stock"] > 0:
            tags.append(f"متبقي {it['stock']}")
        if tags:
            c.text("  ·  ".join(tags), x1 - 12, y0 + ih + 38, 12, INK3, anchor="rm", max_w=tw - 24, weight=MEDIUM)
        pnum, psize = _fit_num(it["price"], 19, tw - 62, 12)
        c.coin(x1 - 20, y0 + th - 14, 8)
        c.text(pnum, x1 - 34, y0 + th - 14, psize, (GOLD_D if affordable else INK3), anchor="rm")
    if not items:
        c.text("المتجر فارغ حاليًا", LW / 2, 190, 26, INK2)
    c.text("اختر منتجًا من القائمة بالأسفل لعرض التفاصيل والشراء", LW / 2, 342, 12.5, INK3, weight=MEDIUM)
    return c.png()


# ---------------------------------------------------------------------- الموسيقى
def _clock(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02}:{sec:02}" if h else f"{m}:{sec:02}"


def render_nowplaying(*, title: str, requester: str, duration: str, thumbnail: Optional[bytes], queue_len: int,
                      volume: int, loop: bool, guild_name: str, status: str = "يعمل الآن", artist: Optional[str] = None,
                      artist_is_channel: bool = False, elapsed: Optional[float] = None, total: int = 0,
                      paused: bool = False, next_title: Optional[str] = None, idle: bool = False) -> bytes:
    """بطاقة الأغنية: غلاف كبير، عنوان، فنان/قناة، شريط تقدّم (فقط عند توفر وقت فعلي) وشارات صغيرة.

    الأزرار والطابور تأتي تحت هذه البطاقة في رسالة اللوحة نفسها (cogs/music.py)، لذا لا تُكرَّر هنا.
    """
    c = Canvas("music")
    t = c.theme
    c.header("الموسيقى", name_of(guild_name, 28), "متوقف مؤقتًا" if paused else ("لا شيء يعمل" if idle else status))
    c.hero((M, 66, LW - M, 334), 26, accent=WARN if paused else t.accent)
    box = (M + 16, 84, M + 16 + 288, 84 + 162)
    im = _cover(thumbnail, px(box[2] - box[0]), px(box[3] - box[1])) if thumbnail and not idle else None
    mx, my = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    if im is not None:
        _paste_rounded(c, im, box, 16)
    else:
        c.rr(box, 16, fill=(255, 255, 255, 22), outline=(255, 255, 255, 50), width=1)
        c.circle(mx, my, 32, None, tuple(t.accent) + (255,), 3)
        c.poly([(mx - 10, my - 16), (mx - 10, my + 16), (mx + 18, my)], tuple(t.accent) + (255,))
    if paused:   # علامة «إيقاف مؤقت» فوق الغلاف
        c.rr(box, 16, fill=(12, 18, 40, 150))
        c.rr((mx - 17, my - 25, mx - 5, my + 25), 4, fill=WHITE + (255,))
        c.rr((mx + 5, my - 25, mx + 17, my + 25), 4, fill=WHITE + (255,))
    tx = LW - M - 20
    mw = tx - (box[2] + 22)
    if idle:
        c.text("انتهت قائمة التشغيل", tx, 130, 28, WHITE, anchor="rm", max_w=mw)
        c.text("اطلب أغنية جديدة عبر أمر play", tx, 168, 17, SUBTLE, anchor="rm", max_w=mw, weight=MEDIUM)
    else:
        c.chip("متوقف مؤقتًا" if paused else "يعمل الآن", tx, 98, "r", WARN if paused else t.accent, solid=True,
               size=12.5, h=22, dot=False)
        lines = c.wrap(title, 26, mw, 2)
        y = 134
        for i, line in enumerate(lines):
            c.text(line, tx, y + i * 33, 26, WHITE, anchor="rm", max_w=mw)
        y += len(lines) * 33 + 2
        if artist:
            c.text(name_of(artist, 34), tx, y + 8, 17, tuple(mix(t.accent, WHITE, 0.5)), anchor="rm", max_w=mw)
            y += 28
        c.text(f"بطلب من {name_of(requester, 20)}", tx, y + 10, 14, SUBTLE, anchor="rm", max_w=mw, weight=MEDIUM)
    # شريط التقدّم: يظهر فقط عندما يتوفر وقت فعلي (عدّاد إطارات الصوت المُرسَلة) ومدة معروفة
    bar = (M + 16 + 50, 268, LW - M - 16 - 50, 277)
    if not idle and elapsed is not None and total > 0:
        c.rr(bar, 4.5, fill=(255, 255, 255, 34))
        frac = min(1.0, elapsed / total)
        w = max(9, (bar[2] - bar[0]) * frac)
        c.rr((bar[2] - w, bar[1], bar[2], bar[3]), 4.5, fill=tuple(WARN if paused else t.accent) + (255,))
        c.circle(bar[2] - w, 272.5, 7, WHITE + (255,))
        c.text(_clock(elapsed), bar[2] + 27, 272.5, 14, WHITE)
        c.text(_clock(total), bar[0] - 27, 272.5, 14, SUBTLE)
    elif not idle:
        c.text(duration or "—", LW / 2, 272.5, 14, SUBTLE, weight=MEDIUM)
    chips = [f"الصوت {volume}%", f"الطابور {queue_len}"] + (["تكرار مفعّل"] if loop else [])
    x = LW - M - 16
    for label in chips:
        w = text_width(label, 13.5) + 26
        c.rr((x - w, 296, x, 322), 13, fill=GLASS)
        c.text(label, x - w / 2, 309, 13.5, WHITE)
        x -= w + 8
    return c.png()


# ---------------------------------------------------------------------- سجل التحذيرات
STAGE_COLORS = {0: GOOD, 1: WARN, 2: (240, 110, 40), 3: BAD}


def render_warnings(*, name: str, avatar: Optional[bytes], active: int, revoked: int, stage: str,
                    items: Sequence[dict], page: int, pages: int, guild_name: str) -> bytes:
    """items: [{id, date, mod, reason, status('active'|'revoked'), note}] حتى 4 صفوف في الصفحة."""
    c = Canvas("warnings")
    col = STAGE_COLORS.get(min(active, 3), BAD)
    c.header("سجل التحذيرات", name_of(guild_name, 28), f"صفحة {page} من {pages}")
    c.card((M, 66, LW - M, 140), 22, accent=col)
    c.avatar(avatar, LW - M - 22 - 28, 103, 52, col, name)
    nx = LW - M - 22 - 56 - 14
    c.text(name_of(name, 26), nx, 91, 22, INK, anchor="rm", max_w=250)
    c.chip(stage, nx, 117, "r", col, size=12.5, h=22, dot=True)
    for i, (k, v, cc) in enumerate((("نشطة", active, col), ("ملغاة", revoked, INK3))):
        x1 = M + 14 + (2 - i) * 82 - (2 - i) * 0
        c.tile((M + 14 + (1 - i) * 90, 80, M + 14 + (1 - i) * 90 + 82, 126), k, _num(v), cc, 22)
    top, rh = 150, 44
    for i, it in enumerate(items[:4]):
        y = top + i * (rh + 4)
        live = it["status"] == "active"
        c.card((M, y, LW - M, y + rh), 15, outline=LINE, fill=SURFACE if live else (246, 248, 252))
        if live:
            c.rr((LW - M - 5, y + 9, LW - M - 1, y + rh - 9), 2, fill=tuple(col) + (255,))
        c.chip(f"#{it['id']}", LW - M - 18, y + 14, "r", col if live else INK3, size=11, h=18)
        c.text(f"{it['date']}", LW - M - 18 - 58, y + 14, 11.5, INK3, anchor="rm", weight=MEDIUM)
        c.text("•  " + name_of(it["mod"], 16), LW - M - 18 - 58 - 70, y + 14, 11.5, INK3, anchor="rm", max_w=140,
               weight=MEDIUM)
        c.text(it["reason"] or "بدون سبب", LW - M - 18, y + 31, 14, INK if live else INK3, anchor="rm",
               max_w=LW - 2 * M - 150, min_scale=0.8)
        c.chip("نشط" if live else "ملغى", M + 16, y + rh / 2, "l", GOOD if live else INK3, size=12, h=22, dot=True)
    if not items:
        c.card((M, 154, LW - M, 300), 24)
        c.circle(LW / 2, 208, 26, GOOD + (40,), GOOD + (255,), 2.5)
        c.line((LW / 2 - 11, 208), (LW / 2 - 3, 217), GOOD + (255,), 4)
        c.line((LW / 2 - 3, 217), (LW / 2 + 12, 199), GOOD + (255,), 4)
        c.text("سجل نظيف", LW / 2, 256, 24, INK)
        c.text("لا توجد تحذيرات على هذا العضو", LW / 2, 282, 14, INK2, weight=MEDIUM)
    return c.png()

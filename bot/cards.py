"""قوالب البطاقات (إنفوجرافيك) على الطبقة الموحّدة bot/canvas.py — كلها 16:9 بدقة 1280×720.

كل قالب دالة نقية: تستقبل بيانات حقيقية جاهزة من النظام (أرصدة، ترتيب، منتجات...) وتُرجع bytes لـ PNG.
تغيير الألوان/الخلفيات يتم في canvas.py مرة واحدة. لا يُولَّد شيء بالذكاء الاصطناعي: رسم برمجي سريع (~50ms).
"""
from __future__ import annotations

import io
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from . import canvas as cv
from .canvas import (AMBER, GOLD, GOLD_L, GREEN, LH, LW, MUTED, PANEL, RED, SOFT, WHITE, Canvas, mix, px, text_width)
from .wheel import clean_name

available = cv.available


def name_of(name: str, limit: int = 24) -> str:
    return clean_name(name, limit) or "عضو"


def _num(n: int) -> str:
    return f"{n:,}"


def coin(c: Canvas, cx: float, cy: float, r: float = 9):
    c.circle(cx, cy, r, GOLD + (255,), GOLD_L + (255,), 1.2)
    c.circle(cx, cy, r * 0.52, None, (176, 122, 32, 255), 1.2)


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
    c.rr((cx - s, cy - s * 0.55, cx + s, cy + s * 0.9), 5, fill=col + (230,))
    c.rr((cx - s * 1.1, cy - s * 0.95, cx + s * 1.1, cy - s * 0.4), 5, fill=mix(col, WHITE, 0.25) + (255,))
    c.rr((cx - s * 0.16, cy - s * 0.95, cx + s * 0.16, cy + s * 0.9), 2, fill=GOLD_L + (255,))
    c.circle(cx - s * 0.32, cy - s * 1.1, s * 0.28, None, GOLD_L + (255,), 2)
    c.circle(cx + s * 0.32, cy - s * 1.1, s * 0.28, None, GOLD_L + (255,), 2)


def _star(c: Canvas, cx: float, cy: float, r: float, col):
    import math
    pts = []
    for i in range(10):
        rad = r if i % 2 == 0 else r * 0.45
        a = math.radians(-90 + i * 36)
        pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
    c.poly(pts, col + (255,))


# ---------------------------------------------------------------------- المحفظة
def render_balance(*, name: str, avatar: Optional[bytes], currency: str, balance: int, rank: int, total_earned: int,
                   games_won: int, earned_today: int, daily_cap: int, guild_name: str) -> bytes:
    c = Canvas("balance")
    c.header(None, False, None, title=f"محفظة {name_of(name, 18)}")
    c.card((22, 52, LW - 22, 214), 24, fill=PANEL + (232,), outline=GOLD + (170,), width=2)
    c.avatar(avatar, LW - 22 - 28 - 52, 133, 100, GOLD, name)
    nx = LW - 22 - 28 - 52 - 74
    c.text(name_of(name, 22), nx, 100, 28, WHITE, anchor="rm", max_w=330)
    c.text("الرصيد الحالي", nx, 128, 14, MUTED, anchor="rm", shadow=False)
    # الرصيد بخط كبير مع عملة مرسومة
    big = _num(balance)
    size = 66
    while text_width(big, size) > 300 and size > 30:
        size -= 4
    c.text(big, nx - 4, 168, size, GOLD_L, anchor="rm")
    coin(c, 52, 168, 22)
    c.text(currency, 92, 168, 20, SOFT, anchor="lm", max_w=150, shadow=False)
    stats = [("الترتيب", f"#{rank}"), ("ألعاب فاز بها", _num(games_won)), ("إجمالي المكتسب", _num(total_earned))]
    w = (LW - 44 - 2 * 12) / 3
    for i, (k, v) in enumerate(stats):
        x1 = LW - 22 - i * (w + 12)
        c.stat((x1 - w, 226, x1, 292), k, v, WHITE, 24)
    if daily_cap:
        frac = min(1.0, earned_today / daily_cap)
        c.text("كسب اليوم من الألعاب", LW - 26, 308, 13, MUTED, anchor="rm", shadow=False)
        c.text(f"{_num(earned_today)} / {_num(daily_cap)}", 26, 308, 13, SOFT, anchor="lm", shadow=False)
        c.progress((26, 316, LW - 26, 328), frac, GOLD if frac < 0.9 else AMBER)
    c.footer(guild_name)
    return c.png()


# ---------------------------------------------------------------------- الملف الشخصي
def render_profile(*, name: str, tag: str, avatar: Optional[bytes], balance: int, currency: str, rank: int,
                   games_won: int, total_earned: int, warnings: int, roles: int, joined: str, created: str,
                   top_role: str, guild_name: str) -> bytes:
    c = Canvas("profile")
    c.header(None, False, top_role and name_of(top_role, 16) or None, title="الملف الشخصي")
    c.card((22, 52, LW - 22, 186), 24, fill=PANEL + (232,), outline=c.theme.accent + (170,), width=2)
    c.avatar(avatar, LW - 22 - 28 - 48, 119, 92, c.theme.accent, name)
    nx = LW - 22 - 28 - 48 - 66
    c.text(name_of(name, 22), nx, 98, 30, WHITE, anchor="rm", max_w=380)
    c.text(tag, nx, 128, 15, MUTED, anchor="rm", shadow=False, max_w=380)
    coin(c, 52, 100, 14)
    c.text(_num(balance), 76, 100, 30, GOLD_L, anchor="lm", max_w=150)
    c.text(currency, 76, 130, 14, MUTED, anchor="lm", shadow=False, max_w=150)
    items = [("الترتيب", f"#{rank}"), ("فوز بالألعاب", _num(games_won)), ("إجمالي المكتسب", _num(total_earned)),
             ("التحذيرات", _num(warnings)), ("الرتب", _num(roles))]
    cols = 5
    w = (LW - 44 - (cols - 1) * 10) / cols
    for i, (k, v) in enumerate(items):
        x1 = LW - 22 - i * (w + 10)
        color = RED if (k == "التحذيرات" and warnings) else WHITE
        c.stat((x1 - w, 200, x1, 266), k, v, color, 22)
    c.rr((22, 280, LW - 22, 334), 16, fill=(255, 255, 255, 20), outline=(255, 255, 255, 50), width=1)
    c.text(f"انضم للسيرفر: {joined}", LW - 40, 298, 15, SOFT, anchor="rm", shadow=False)
    c.text(f"إنشاء الحساب: {created}", LW - 40, 321, 15, MUTED, anchor="rm", shadow=False)
    c.footer(guild_name)
    return c.png()


# ---------------------------------------------------------------------- المتصدرون
MEDALS = {1: (255, 205, 84), 2: (196, 204, 220), 3: (221, 141, 82)}


def render_leaderboard(*, rows: Sequence[dict], avatars: dict[int, Optional[bytes]], viewer_id: int,
                       viewer_rank: int, viewer_balance: int, currency: str, guild_name: str, page: int,
                       pages: int) -> bytes:
    """rows: [{rank, user_id, name, balance}] حتى 10 صفوف."""
    c = Canvas("board")
    c.header(None, False, f"صفحة {page} من {pages}", title=f"متصدرو {name_of(guild_name, 16)}")
    col_w, row_h, top = (LW - 44 - 14) / 2, 50, 54
    for i, r in enumerate(rows[:10]):
        col, row = divmod(i, 5)
        x1 = LW - 22 - col * (col_w + 14)
        x0, y0 = x1 - col_w, top + row * (row_h + 6)
        me = r["user_id"] == viewer_id
        medal = MEDALS.get(r["rank"])
        c.rr((x0, y0, x1, y0 + row_h), 14, fill=(GOLD + (46,) if me else PANEL + (226,)),
             outline=((medal or c.theme.accent) + (200,)), width=1.6 if medal or me else 1)
        bx = x1 - 26
        if medal:
            c.circle(bx, y0 + row_h / 2, 15, medal + (255,), None)
            c.text(str(r["rank"]), bx, y0 + row_h / 2, 18, (40, 28, 6), shadow=False)
        else:
            c.text(f"#{r['rank']}", bx, y0 + row_h / 2, 16, MUTED, shadow=False)
        c.avatar(avatars.get(r["user_id"]), x1 - 82, y0 + row_h / 2, 34, medal or c.theme.accent, r["name"])
        tx = x1 - 108
        c.text(name_of(r["name"], 22), tx, y0 + 16, 17, WHITE, anchor="rm", max_w=col_w - 140)
        num = _num(r["balance"])
        c.text(num, tx - 22, y0 + 35, 19, GOLD_L, anchor="rm", max_w=col_w - 190)
        coin(c, tx - 8, y0 + 35, 8)
        if me:
            c.pill((x0 + 10, y0 + row_h / 2 - 11, x0 + 56, y0 + row_h / 2 + 11), GOLD + (70,), GOLD_L + (220,), 1)
            c.text("أنت", x0 + 33, y0 + row_h / 2, 13, (255, 232, 160), shadow=False)
    if not rows:
        c.text("لا أحد في الترتيب بعد — العب واجمع النقاط!", LW / 2, 180, 24, MUTED, shadow=False)
    c.rr((22, 326, LW - 22, 350), 12, fill=(255, 255, 255, 20))
    c.text(f"ترتيبك  #{viewer_rank}", LW - 36, 338, 15, WHITE, anchor="rm", shadow=False)
    c.text(f"{_num(viewer_balance)} {currency}", 36, 338, 15, GOLD_L, anchor="lm", shadow=False, max_w=260)
    return c.png()


# ---------------------------------------------------------------------- المتجر
def render_store(*, items: Sequence[dict], images: dict[int, Optional[bytes]], balance: int, currency: str,
                 guild_name: str, page: int, pages: int) -> bytes:
    """items: [{id, name, price, kind, stock, duration_hours}] حتى 6 منتجات."""
    c = Canvas("shop")
    t = c.theme
    c.header(f"رصيدك {_num(balance)}", False, f"صفحة {page} من {pages}", title=f"متجر {name_of(guild_name, 16)}")
    cols, rows = 3, 2
    gx, gy = 12, 12
    tw = (LW - 40 - (cols - 1) * gx) / cols
    th = (326 - 52 - (rows - 1) * gy) / rows
    for i, it in enumerate(items[:6]):
        r, col = divmod(i, cols)
        x1 = LW - 20 - col * (tw + gx)
        x0, y0 = x1 - tw, 52 + r * (th + gy)
        sold = it["stock"] == 0
        affordable = balance >= it["price"]
        c.card((x0, y0, x1, y0 + th), 16, fill=PANEL + (232,),
             outline=(RED if sold else (t.accent if affordable else MUTED)) + (190,), width=1.6)
        # منطقة الصورة
        ih = th * 0.44
        box = (x0 + 8, y0 + 8, x1 - 8, y0 + 8 + ih)
        data = images.get(it["id"])
        im = _cover(data, px(box[2] - box[0]), px(box[3] - box[1])) if data else None
        if im is not None:
            _paste_rounded(c, im, box, 10)
        else:
            c.rr(box, 10, fill=mix(PANEL, t.accent, 0.16) + (255,))
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if it["kind"] == "role":
                _star(c, cx, cy, 20, GOLD)
            else:
                _gift(c, cx, cy + 2, 16, t.accent)
        c.pill((x1 - 44, y0 + 12, x1 - 12, y0 + 30), (0, 0, 0, 150))
        c.text(f"#{it['id']}", x1 - 28, y0 + 21, 11.5, SOFT, shadow=False)
        if sold:
            c.rr(box, 10, fill=(10, 8, 20, 170))
            c.text("نفد", (box[0] + box[2]) / 2, (box[1] + box[3]) / 2, 26, RED)
        c.text(name_of(it["name"], 40), x1 - 12, y0 + ih + 22, 15, WHITE, anchor="rm", max_w=tw - 24)
        coin(c, x1 - 22, y0 + th - 17, 9)
        c.text(_num(it["price"]), x1 - 38, y0 + th - 17, 20, GOLD_L if affordable else MUTED, anchor="rm",
               max_w=tw - 60)
        tags = []
        if it["duration_hours"]:
            h = it["duration_hours"]
            tags.append(f"{h // 24} يوم" if h % 24 == 0 else f"{h} ساعة")
        if it["stock"] > 0:
            tags.append(f"متبقي {it['stock']}")
        if tags:
            c.text("  ·  ".join(tags), x1 - 12, y0 + ih + 41, 12.5, MUTED, anchor="rm", shadow=False, max_w=tw - 24)
    if not items:
        c.text("المتجر فارغ حاليًا", LW / 2, 180, 28, MUTED, shadow=False)
    c.text("اختر منتجًا من القائمة بالأسفل لعرض التفاصيل والشراء", LW / 2, 342, 13.5, MUTED, shadow=False)
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
    """لوحة الموسيقى: غلاف، عنوان، فنان/قناة، تقدّم (فقط عند توفر وقت فعلي)، حالة، صوت، قائمة، والتالي."""
    c = Canvas("music")
    t = c.theme
    c.header(None, False, "متوقف مؤقتًا" if paused else ("لا شيء يعمل" if idle else status))
    c.card((22, 52, LW - 22, 338), 24, fill=PANEL + (232,), outline=(AMBER if paused else t.accent) + (170,), width=2)
    box = (34, 66, 34 + 240, 66 + 135)
    im = _cover(thumbnail, px(box[2] - box[0]), px(box[3] - box[1])) if thumbnail and not idle else None
    if im is not None:
        _paste_rounded(c, im, box, 14)
    else:
        c.rr(box, 14, fill=mix(PANEL, t.accent, 0.22) + (255,))
        c.circle(154, 133, 32, None, t.accent + (255,), 3)
        c.poly([(142, 117), (142, 149), (170, 133)], t.accent + (255,))
    if paused:   # علامة «إيقاف مؤقت» فوق الغلاف
        c.rr(box, 14, fill=(8, 7, 18, 140))
        c.rr((133, 108, 147, 158), 4, fill=WHITE + (255,))
        c.rr((161, 108, 175, 158), 4, fill=WHITE + (255,))
    tx = LW - 40
    if idle:
        c.text("انتهت قائمة التشغيل", tx, 110, 26, WHITE, anchor="rm", max_w=300)
        c.text("اطلب أغنية جديدة بـ /play", tx, 146, 16, MUTED, anchor="rm", shadow=False)
    else:
        lines = c.wrap(title, 24, 296, 2)
        for i, line in enumerate(lines):
            c.text(line, tx, 88 + i * 31, 24, WHITE, anchor="rm", max_w=296)
        y = 88 + len(lines) * 31 + 4
        if artist:
            c.text(("القناة: " if artist_is_channel else "الفنان: ") + name_of(artist, 28), tx, y + 4, 15, SOFT,
                   anchor="rm", shadow=False, max_w=296)
            y += 26
        c.text(f"بطلب من {name_of(requester, 20)}", tx, y + 6, 14.5, MUTED, anchor="rm", shadow=False, max_w=296)
    # شريط التقدم: يظهر فقط عندما يتوفر وقت فعلي (عدّاد إطارات الصوت المُرسَلة) ومدة معروفة
    if not idle and elapsed is not None and total > 0:
        frac = min(1.0, elapsed / total)
        c.progress((34 + 52, 214, LW - 34 - 52, 224), frac, AMBER if paused else t.accent)
        c.text(_clock(elapsed), LW - 34 - 24, 219, 13, SOFT, shadow=False)
        c.text(_clock(total), 34 + 24, 219, 13, MUTED, shadow=False)
    chips = [("المدة", duration if not idle else "—"), ("الصوت", f"{volume}%"), ("القائمة", str(queue_len))]
    if loop:
        chips.append(("التكرار", "مفعّل"))
    w = (LW - 68 - (len(chips) - 1) * 10) / len(chips)
    for i, (k, v) in enumerate(chips):
        x1 = LW - 34 - i * (w + 10)
        c.stat((x1 - w, 238, x1, 300), k, v, WHITE, 22)
    if next_title and not idle:
        c.text("التالي: " + next_title, LW / 2, 318, 14, MUTED, shadow=False, max_w=LW - 90)
    c.footer(guild_name)
    return c.png()


# ---------------------------------------------------------------------- سجل التحذيرات
STAGE_COLORS = {0: GREEN, 1: AMBER, 2: (255, 138, 76), 3: RED}


def render_warnings(*, name: str, avatar: Optional[bytes], active: int, revoked: int, stage: str,
                    items: Sequence[dict], page: int, pages: int, guild_name: str) -> bytes:
    """items: [{id, date, mod, reason, status('active'|'revoked'), note}] حتى 4 صفوف في الصفحة."""
    c = Canvas("warnings")
    col = STAGE_COLORS.get(min(active, 3), RED)
    c.header(None, False, f"صفحة {page} من {pages}", title="سجل التحذيرات")
    c.card((22, 52, LW - 22, 130), 20, fill=PANEL + (232,), outline=col + (190,), width=2)
    c.avatar(avatar, LW - 22 - 24 - 28, 91, 56, col, name)
    nx = LW - 22 - 24 - 56 - 14
    c.text(name_of(name, 22), nx, 77, 24, WHITE, anchor="rm", max_w=250)
    c.pill((nx - 150, 94, nx, 116), col + (50,), col + (230,), 1.2)
    c.text(stage, nx - 75, 105, 14, WHITE, shadow=False, max_w=140)
    c.stat((30, 60, 118, 122), "نشطة", _num(active), col, 22)
    c.stat((126, 60, 214, 122), "ملغاة", _num(revoked), MUTED, 22)
    top, rh = 138, 46
    for i, it in enumerate(items[:4]):
        y = top + i * (rh + 4)
        live = it["status"] == "active"
        c.rr((22, y, LW - 22, y + rh), 14, fill=PANEL + ((226 if live else 150),), outline=(col if live else MUTED) + (150,),
             width=1.2)
        c.pill((LW - 34 - 52, y + 6, LW - 34, y + 24), (col if live else MUTED) + (60,), None)
        c.text(f"#{it['id']}", LW - 34 - 26, y + 15, 13, WHITE if live else MUTED, shadow=False)
        c.text(f"{it['date']}  ·  {name_of(it['mod'], 16)}", LW - 34 - 62, y + 15, 12.5, MUTED, anchor="rm",
               shadow=False, max_w=300)
        c.text(it["reason"] or "بدون سبب", LW - 34, y + 33, 14.5, WHITE if live else MUTED, anchor="rm",
               max_w=LW - 150, shadow=False)
        chip = "نشط" if live else "ملغى"
        c.pill((34, y + 12, 34 + 62, y + 34), (GREEN if live else MUTED) + (50,), (GREEN if live else MUTED) + (190,), 1)
        c.text(chip, 34 + 31, y + 23, 13.5, GREEN if live else MUTED, shadow=False)
    if not items:
        c.text("سجل نظيف — لا تحذيرات", LW / 2, 230, 26, MUTED, shadow=False)
    c.footer(guild_name)
    return c.png()

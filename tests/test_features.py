"""اختبارات بدون اتصال للميزات الجديدة: الصلاحيات، لوحة التحكم، منع تكرار المكافآت، وعجلة الروليت.

التشغيل:  python tests/test_features.py
"""
import asyncio
import os
import random
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discord  # noqa: E402

from bot import wheel  # noqa: E402
from bot.checks import allowed, has_category, is_admin  # noqa: E402
from bot.db import Database, LimitReached  # noqa: E402
from bot.game_logic import GuessState, make_math_problem, parse_number, quiz_options, scramble  # noqa: E402
from bot.settings_schema import SETTINGS, SETTINGS_BY_KEY, parse_value  # noqa: E402
from cogs import panel as panel_mod  # noqa: E402
from cogs.games import GAMES, GameContext, Games  # noqa: E402

GUILD = 1
OWNER = 999


# ---------------------------------------------------------------------- أدوات وهمية
def fake_member(uid: int, *, admin=False, role_ids=(), guild_id=GUILD, bot=False, **perms):
    m = MagicMock(spec=discord.Member)
    m.id = uid
    m.bot = bot
    m.mention = f"<@{uid}>"
    m.display_name = f"user{uid}"
    m.guild_permissions = discord.Permissions(administrator=admin, **perms)
    m.roles = [SimpleNamespace(id=r) for r in role_ids]
    m.guild = SimpleNamespace(id=guild_id, owner_id=OWNER)
    return m


def fake_interaction(user, guild_id=GUILD):
    i = MagicMock()
    i.user = user
    i.guild_id = guild_id
    i.guild = MagicMock()
    i.guild.id = guild_id
    i.response = MagicMock()
    i.response.is_done = MagicMock(return_value=False)
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.defer = AsyncMock(side_effect=lambda *a, **k: setattr(i.response, "is_done", MagicMock(return_value=True)))
    i.response.send_modal = AsyncMock()
    i.edit_original_response = AsyncMock()
    return i


def fake_bot(db):
    guild = MagicMock()
    guild.id = GUILD
    guild.name = "Test"
    guild.icon = None
    return SimpleNamespace(db=db, get_guild=lambda _gid: guild, send_log=AsyncMock())


async def temp_db(d):
    db = Database(os.path.join(d, "t.db"))
    await db.connect()
    return db


# ---------------------------------------------------------------------- الصلاحيات
async def test_permissions():
    with tempfile.TemporaryDirectory() as d:
        db = await temp_db(d)
        admin = fake_member(1, admin=True)
        owner = fake_member(OWNER)
        normal = fake_member(2)
        manager = fake_member(3, manage_guild=True)
        staff = fake_member(4, role_ids=(500,))

        assert is_admin(admin) and is_admin(owner) and not is_admin(normal) and not is_admin(manager)
        for cat in ("economy", "shop", "events", "moderation"):
            assert await has_category(db, admin, cat)
            assert not await has_category(db, normal, cat), cat
        # بدون رتب محددة: يُعتمد على صلاحية ديسكورد الافتراضية
        assert await has_category(db, manager, "economy") and await has_category(db, manager, "shop")
        assert not await allowed(db, manager, "admin")
        # بعد تحديد رتبة للفئة: الرتبة فقط تمنح الصلاحية، والصلاحية الافتراضية تُتجاهل
        await db.set_perm_roles(GUILD, "economy", [500])
        assert await has_category(db, staff, "economy")
        assert not await has_category(db, manager, "economy")
        assert not await has_category(db, staff, "shop")
        # الرتب تخص سيرفرها فقط
        other_guild_staff = fake_member(5, role_ids=(500,), guild_id=2)
        assert not await has_category(db, other_guild_staff, "economy")
        # غير الأعضاء (رسائل خاصة) مرفوضون
        assert not await has_category(db, SimpleNamespace(id=6), "economy")
        await db.close()
    print("OK permissions: admin/owner, role mapping, fallback, cross-guild isolation")


# ---------------------------------------------------------------------- لوحة التحكم
async def test_panel_security_and_layout():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            await _panel_checks(db)
        finally:
            await db.close()
    print("OK panel: owner/guild lock, server-side re-check, forged values, layout limits")


async def _panel_checks(db):
    bot = fake_bot(db)
    admin = fake_member(1, admin=True)
    normal = fake_member(2)
    shop_mgr = fake_member(3, manage_guild=True)

    # كل مجموعات الإعدادات لها قسم (الجديدة تُضاف تلقائيًا)
    mapped = {g for s in panel_mod.SECTIONS for g in s.groups}
    assert {s.group for s in SETTINGS} <= mapped
    assert len(panel_mod.SECTIONS) <= 24  # + "الرئيسية" ضمن حد القائمة (25)

    # المستخدم العادي لا يرى أي قسم => الأمر مرفوض
    assert await panel_mod.visible_sections(db, normal) == []
    keys = {s.key for s in await panel_mod.visible_sections(db, shop_mgr)}
    assert "shop" in keys and "economy" in keys and "permissions" not in keys and "channels" not in keys

    view = panel_mod.PanelView(bot, admin, MagicMock(id=GUILD))
    # بناء كل قسم وكل وضع: لا تجاوز لحدود ديسكورد (5 صفوف، 25 عنصرًا، 25 خيارًا)
    await db.add_shop_item(GUILD, "VIP", "", 50, "custom", None, -1, 0, True)
    for sec in [None] + panel_mod.SECTIONS:
        view.section, view.mode = sec, None
        embed = await view.build(admin)
        assert len(view.children) <= 25 and len(embed) <= 6000, sec
        for child in view.children:
            if isinstance(child, discord.ui.Select):
                assert len(child.options) <= 25
    view.section = panel_mod.SECTIONS_BY_KEY["channels"]
    view.mode, view.target = "channel", "log_channel"
    await view.build(admin)
    view.section, view.mode, view.target = panel_mod.SECTIONS_BY_KEY["permissions"], "perm_roles", "economy"
    await view.build(admin)

    # 1) مستخدم آخر يضغط على لوحة ليست له
    intruder = fake_interaction(normal)
    assert not await view.interaction_check(intruder)
    intruder.response.send_message.assert_awaited()

    # 2) لوحة سيرفر آخر
    other = fake_interaction(admin, guild_id=2)
    assert not await view.interaction_check(other)

    # 3) صاحب اللوحة فقد صلاحية المسؤول أثناء فتحها: تُرفض العملية من الخادم
    demoted = fake_member(1)  # نفس الآيدي بلا صلاحيات
    inter = fake_interaction(demoted)
    assert not await view.guard(inter, "admin")
    before = (await db.get_settings(GUILD))["antilink_enabled"]
    view.section, view.mode = panel_mod.SECTIONS_BY_KEY["protection"], None

    async def pick(key, user):  # محاكاة اختيار العميل من قائمة الإعدادات المعروضة
        select = panel_mod.SettingSelect([SETTINGS_BY_KEY["antilink_enabled"]], await db.get_settings(GUILD))
        view.clear_items()
        view.add_item(select)
        select._values = [key]
        await select.callback(fake_interaction(user))

    await pick("antilink_enabled", demoted)
    assert (await db.get_settings(GUILD))["antilink_enabled"] == before, "non-admin must not change settings"

    # 4) المسؤول يقلب الإعداد فعلًا ويُحفظ في القاعدة
    await pick("antilink_enabled", admin)
    assert (await db.get_settings(GUILD))["antilink_enabled"] == (not before)

    # 5) إعداد من قسم آخر (قيمة مزوّرة) يُرفض
    await pick("reward_quiz", admin)
    assert (await db.get_settings(GUILD))["reward_quiz"] == SETTINGS_BY_KEY["reward_quiz"].default

    # 6) نافذة المبلغ: عضو بلا صلاحية الاقتصاد لا يستطيع إضافة نقاط حتى لو أرسل النافذة
    target = fake_member(50)
    modal = panel_mod.PointsModal(view, "add", target)
    modal.amount._value = "1000"
    await modal.on_submit(fake_interaction(normal))
    assert await db.balance(GUILD, 50) == 0


# ---------------------------------------------------------------------- منع تكرار المكافآت
async def test_reward_idempotency():
    with tempfile.TemporaryDirectory() as d:
        db = await temp_db(d)
        # نفس المفتاح مرتين = صرف واحد
        assert await db.game_reward(GUILD, 7, 40, "x", 0, claim_key="k1") == 40
        assert await db.game_reward(GUILD, 7, 40, "x", 0, claim_key="k1") is None
        # 20 طلبًا متزامنًا بنفس المفتاح = صرف واحد فقط
        res = await asyncio.gather(*[db.game_reward(GUILD, 8, 25, "x", 0, claim_key="k2") for _ in range(20)])
        assert sum(1 for r in res if r) == 1 and await db.balance(GUILD, 8) == 25
        assert (await db.wallet(GUILD, 8))["games_won"] == 1

        # Games._award: استدعاءات متزامنة لنفس الجولة لا تكرر الجائزة
        bot = fake_bot(db)
        cog = Games(bot)
        winner = fake_member(9)
        winner.created_at = discord.utils.utcnow() - __import__("datetime").timedelta(days=400)
        winner.joined_at = discord.utils.utcnow() - __import__("datetime").timedelta(days=100)
        ctx = GameContext(game="quiz", reward=30, is_event=False, starter=winner, channel_id=1)
        guild = SimpleNamespace(id=GUILD)
        lines = await asyncio.gather(*[cog._award(guild, winner, ctx) for _ in range(5)])
        assert sum(1 for x in lines if "+" in x) == 1 and await db.balance(GUILD, 9) == 30

        # حساب جديد جدًا لا يكسب
        fresh = fake_member(10)
        fresh.created_at = discord.utils.utcnow()
        fresh.joined_at = discord.utils.utcnow()
        ctx2 = GameContext(game="quiz", reward=30, is_event=False, starter=fresh, channel_id=1)
        assert "لم تُضف" in await cog._award(guild, fresh, ctx2) and await db.balance(GUILD, 10) == 0
        await db.close()
    print("OK rewards: claim keys, 20x concurrent claims, concurrent _award, eligibility")


# ---------------------------------------------------------------------- عجلة الروليت
def test_wheel():
    rng = random.Random(42)
    for n in (2, 3, 5, 8, 13, 16, 17, 33, 50):
        for _ in range(200):
            w = wheel.choose_winner(n, rng)
            assert 0 <= w < n
            a = wheel.stop_angle(w, n, spins=rng.randint(0, 6), rng=rng)
            assert wheel.segment_at(a, n) == w, (n, w, a)
            # الاهتزاز الأخير لا يخرج المؤشر من قطاع الفائز
            amp = min(2.5, 360 / n * 0.1)
            assert wheel.segment_at(a + amp, n) == w and wheel.segment_at(a - amp * 0.6, n) == w
    curve = wheel.spin_curve(64)
    assert curve[0] == 0 and abs(curve[-1] - 1) < 1e-9 and all(b >= a for a, b in zip(curve, curve[1:]))
    # التسارع ثم التباطؤ: أكبر سرعة ليست في البداية ولا في النهاية
    steps = [b - a for a, b in zip(curve, curve[1:])]
    peak = steps.index(max(steps))
    assert 0 < peak < len(steps) // 2 and steps[-1] < steps[peak] / 10

    # الأنيميشن يرفض زاوية لا تطابق الفائز المحدد من منطق البوت
    try:
        wheel.render_spin(["a", "b", "c"], 0, wheel.stop_angle(1, 3))
        raise AssertionError("mismatched angle accepted")
    except ValueError:
        pass
    names = ["أحمد", "Sara 🌸", "x" * 40, "نور"]
    spin = wheel.render_spin(names, 2, wheel.stop_angle(2, 4), frames=20)
    assert spin.gif[:6] in (b"GIF87a", b"GIF89a") and len(spin.gif) < 8 * 1024 * 1024
    for png in (wheel.render_lobby([]), wheel.render_lobby(["وحيد"]), wheel.render_result(names, 1, 90)):
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert wheel.clean_name("🔥 Ali 🔥") == "Ali" and len(wheel.clean_name("x" * 50)) <= 14
    print(f"OK wheel: winner == pointer for 1800 spins, easing, GIF {len(spin.gif) // 1024}KB, "
          f"text font: {wheel.has_text_font()}")


# ---------------------------------------------------------------------- منطق الألعاب
def test_game_logic():
    rng = random.Random(1)
    assert parse_number("٤٢") == 42 and parse_number(" 17 ") == 17 and parse_number("abc") is None
    assert parse_number("1" * 20) is None
    for _ in range(500):
        p = make_math_problem(rng)
        expr = p.text.replace("×", "*").replace("÷", "//").replace("−", "-")
        assert eval(expr) == p.answer, p  # noqa: S307 - نص مولّد داخليًا
        assert p.answer >= 0
    for w in ("مدرسة", "كمبيوتر", "abcd"):
        assert sorted(scramble(w, rng)) == sorted(w) and scramble(w, rng) != w
    st = GuessState(secret=40, maximum=100, max_tries=3)
    assert st.guess(1, 500) == "out" and st.remaining(1) == 3        # خارج النطاق لا يستهلك محاولة
    assert st.guess(1, 20) == "higher" and st.low == 21
    assert st.guess(1, 60) == "lower" and st.high == 59
    assert st.guess(1, 50) == "lower" and st.guess(1, 40) == "blocked"  # انتهت محاولاته
    assert st.guess(2, 40) == "win"
    opts = quiz_options("a", ["b", "b", "c", "d"], rng)
    assert "a" in opts and len(opts) == len(set(opts)) == 4
    # كل لعبة لها إعدادات تفعيل ومكافأة ومدة صالحة
    for key, g in GAMES.items():
        assert f"game_{key}_enabled" in SETTINGS_BY_KEY and f"reward_{key}" in SETTINGS_BY_KEY
        assert g.seconds_key in SETTINGS_BY_KEY
    for bad in ("abc", "999999999", "-5"):
        try:
            parse_value(SETTINGS_BY_KEY["quiz_seconds"], bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert parse_value(SETTINGS_BY_KEY["quiz_seconds"], "٣٠") == 30
    assert parse_value(SETTINGS_BY_KEY["games_public"], "لا") is False
    print("OK game logic: numbers, math, scramble, guess state, quiz options, settings validation")


# ---------------------------------------------------------------------- تشغيل الروليت كاملة (محاكاة)
async def test_roulette_flow():
    import datetime
    import cogs.games as games_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = fake_bot(db)
            cog = Games(bot)
            old = discord.utils.utcnow() - datetime.timedelta(days=400)

            def player(uid):
                m = fake_member(uid)
                m.created_at = m.joined_at = old
                m.display_avatar = SimpleNamespace(url="https://example.com/a.png")
                return m

            for mode, count in (("عجلة الحظ", 4), ("إقصاء", 4), ("عجلة الحظ", 1), ("عجلة الحظ", 0)):
                players = [player(100 + i) for i in range(count)]

                async def fake_wait(self, _players=players):
                    self.players.extend(_players)
                    self.stop()
                    return False

                async def kick_wait(self):  # لا يختار أحد => يخرج صاحب الدور
                    self.stop()
                    return True

                games_mod.RouletteLobby.wait = fake_wait
                games_mod.KickView.wait = kick_wait
                inter = fake_interaction(fake_member(1, admin=True))
                inter.app_permissions = discord.Permissions(attach_files=True)
                inter.channel = MagicMock(id=77)
                sent = []
                inter.channel.send = AsyncMock(side_effect=lambda **kw: sent.append(kw) or MagicMock(edit=AsyncMock()))
                message = MagicMock(edit=AsyncMock())
                inter.original_response = AsyncMock(return_value=message)
                before = sum([await db.balance(GUILD, p.id) for p in players])
                await cog.roulette.callback(cog, inter, mode=mode, join_seconds=15, reward=None)
                after = sum([await db.balance(GUILD, p.id) for p in players])
                assert 77 not in cog.active  # اللعبة أُغلقت وتحررت القناة
                cog.last_game.clear()
                if count >= 3:
                    assert after - before == 60, (mode, before, after)  # فائز واحد فقط حصل على الجائزة
                    assert any("مبروك" in (kw.get("content") or "") for kw in sent)
                else:
                    assert after == before
            print("OK roulette flow: wheel + elimination end-to-end, 0/1 player cancel, single payout")
        finally:
            await db.close()


async def test_other_game_flows():
    import datetime
    import cogs.games as games_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            old = discord.utils.utcnow() - datetime.timedelta(days=400)
            counter = iter(range(1, 10_000))

            async def wait_for(_event, check=None, timeout=None):
                if not GUESSING:
                    raise asyncio.TimeoutError
                for _ in range(1000):  # تخمينات متتالية من أعضاء مختلفين حتى يصيب أحدهم
                    n = next(counter)
                    author = fake_member(1000 + n)
                    author.created_at = author.joined_at = old
                    author.display_avatar = SimpleNamespace(url="https://example.com/a.png")
                    msg = MagicMock(content=str(n), author=author, channel=SimpleNamespace(id=77))
                    msg.add_reaction, msg.reply = AsyncMock(), AsyncMock()
                    if check(msg):
                        return msg
                raise asyncio.TimeoutError

            bot = fake_bot(db)
            bot.wait_for = wait_for
            cog = Games(bot)

            async def quiz_wait(self):
                self.stop()
                return True
            games_mod.QuizView.wait = quiz_wait

            for name in ("typing", "scramble", "math", "quiz", "guess"):
                GUESSING = name == "guess"
                inter = fake_interaction(fake_member(1, admin=True))
                inter.channel = MagicMock(id=77)
                inter.channel_id = 77
                inter.channel.send = AsyncMock()
                inter.original_response = AsyncMock(return_value=MagicMock(edit=AsyncMock(), reply=AsyncMock()))
                cmd = getattr(cog, "scramble_cmd" if name == "scramble" else name)
                kwargs = {"reward": None} if name != "guess" else {"maximum": 50, "reward": None}
                await cmd.callback(cog, inter, **kwargs)
                assert 77 not in cog.active, name
                cog.last_game.clear()
            winners = await db.leaderboard(GUILD)
            assert len(winners) == 1 and winners[0]["balance"] == 25  # فائز خمّن الرقم فقط
            print("OK game flows: typing/scramble/math/quiz timeouts + guess win with single payout")
        finally:
            await db.close()


def test_gamecards():
    from PIL import Image
    from bot import gamecards as gc
    assert gc.available()
    import io as _io

    def check(res, expect_gif=None):
        data, ext = res
        im = Image.open(_io.BytesIO(data))
        assert im.size == (gc.W, gc.H) and ext in ("png", "gif")
        assert len(data) < 2 * 1024 * 1024, len(data)
        if expect_gif is not None:
            assert (ext == "gif") == expect_gif and (im.n_frames > 1) == expect_gif
        return im

    # الحساب: كل العمليات + مسائل طويلة، متحرك وثابت
    for expr in ("200 + 200", "384 − 127", "15 × 14", "168 ÷ 12", "999 + 999"):
        check(gc.render_math(expr, 15, "15 نقطة", "سهل"), True)
        check(gc.render_math(expr, 15, None, "", animate=False), False)
    # المؤقت GIF: ينتهي بإطار فارغ طويل المدة، وعدد الإطارات محدود حتى للمدد الطويلة
    im = check(gc.render_math("1 + 1", 120, "5 نقطة"), True)
    assert im.n_frames <= gc.MAX_GIF_FRAMES + 1
    im.seek(im.n_frames - 1)
    assert im.info["duration"] >= 60_000
    # مدة قصيرة جدًا لا تنهار
    check(gc.render_math("1 + 1", 2, "5"), False)
    # الكتابة والتفكيك: كلمات عربية/لاتينية/طويلة
    for w in ("مستشفى", "Keyboard", "x" * 40, "أبجد هوز"):
        check(gc.render_typing(w, 20, "15 نقطة"), True)
    for letters in (list("ىفشتسم"), list("abcd"), list("ابتثجحخدذرزسشصضطظعغفقكلمنهوي")[:12]):
        check(gc.render_scramble(letters, 30, "20 نقطة", hint="تبدأ بـ «م» • عدد الحروف 6"), True)
        check(gc.render_scramble(letters, 30, None, animate=False), False)
    # الأسئلة: سؤال طويل جدًا وخيارات طويلة وأقل من 4 خيارات
    long_q = "ما هو اسم " + "العالم الذي اكتشف " * 12 + "؟"
    check(gc.render_quiz(long_q, ["إجابة طويلة جدًا " * 6, "ب", "ج", "د"], 20, "20 نقطة"), True)
    check(gc.render_quiz("سؤال", ["أ", "ب"], 20, None), True)
    # التخمين: أول النطاق وآخره وبعد التضييق
    check(gc.render_guess(1, 1000, 1000, 0, 5, "25 نقطة"), False)
    check(gc.render_guess(500, 500, 1000, 40, 5, None, last="آخر تخمين: 499 ← الرقم أكبر"), False)
    # النتيجة: فائز باسم طويل/إيموجي/أفاتار تالف/بدون تفاصيل، وانتهاء الوقت
    for headline in ("سارة", "x" * 80, "🔥🔥", ""):
        check(gc.render_result("math", headline, [("الوقت", "2.41 ث"), ("الناتج", "400")], winner=True,
                               avatar=b"not an image"), True)
    check(gc.render_result("quiz", "الرياض", [], winner=True, animate=False), False)
    check(gc.render_result("typing", "كلمة", [("الكلمة", "كلمة")], winner=False), False)
    # صورة أفاتار حقيقية صغيرة
    buf = _io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 90)).save(buf, "PNG")
    check(gc.render_result("guess", "نور", [("الرقم", "42")], winner=True, avatar=buf.getvalue()), True)
    print("OK gamecards: all 5 games + result screens, GIF timer, long/odd text, bad avatar, frame cap")


async def test_visual_fallback():
    """عند تعطيل البطاقات أو غياب صلاحية رفع الملفات تعمل اللعبة بالنص ولا تتعطل."""
    import datetime
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = fake_bot(db)
            cog = Games(bot)
            s = await db.get_settings(GUILD)
            inter = fake_interaction(fake_member(1, admin=True))
            inter.app_permissions = discord.Permissions(attach_files=True, embed_links=True)
            assert cog._visual_ok(inter, s)
            inter.app_permissions = discord.Permissions(attach_files=False, embed_links=True)
            assert not cog._visual_ok(inter, s)
            inter.app_permissions = discord.Permissions(attach_files=True, embed_links=True)
            await db.set_setting(GUILD, "game_visuals", False)
            assert not cog._visual_ok(inter, await db.get_settings(GUILD))
            await db.set_setting(GUILD, "game_visuals", True)

            # فشل الرسم => رجوع للنص بدون استثناء
            from cogs.games import GameContext
            ctx = GameContext(game="math", reward=5, is_event=False, starter=fake_member(1), channel_id=5, visual=True)

            def boom(*_a, **_k):
                raise RuntimeError("render failed")

            assert await cog._render(ctx, boom) is None and ctx.visual is False
            assert await cog._result_image(ctx, s, "x", []) is None
            for key in ("game_visuals", "game_animations"):
                assert key in SETTINGS_BY_KEY and SETTINGS_BY_KEY[key].kind == "bool"
        finally:
            await db.close()
    print("OK visual fallback: setting off, missing Attach Files, render failure -> text mode")


# ====================================================================== الأوامر والاختصارات
UID = 111111111111111111   # آيديات بطول ديسكورد الحقيقي (المحلل يشترط 15+ رقمًا)


async def make_bot(db):
    import main
    from bot import registry
    bot = main.ServerBot()
    bot.db = db
    for c in main.COGS:
        await bot.load_extension(c)
    bot.get_cog("VoiceHome").watchdog.cancel()   # لا اتصال حقيقي في الاختبارات
    bot.get_cog("Shop").recover_loop.cancel()
    registry.apply_docs(bot.tree)
    return bot


def fake_message(content, author, members=None, guild_id=GUILD):
    members = members or {}
    guild = MagicMock()
    guild.id = guild_id
    guild.owner_id = OWNER
    guild.get_member = lambda uid: members.get(uid)
    def _fetch(uid):
        if uid in members:
            return members[uid]
        raise discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")

    guild.fetch_member = AsyncMock(side_effect=_fetch)
    guild.me = MagicMock()
    msg = MagicMock(spec=discord.Message)
    msg.content, msg.author, msg.guild = content, author, guild
    msg.created_at = discord.utils.utcnow()
    msg.id = 1
    msg.channel = MagicMock()
    msg.channel.id = 5
    msg.channel.permissions_for = lambda _m: discord.Permissions.all()
    msg.channel.send = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    msg.reply = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    return msg


def last_embed(msg):
    for call in (msg.reply.await_args_list + msg.channel.send.await_args_list)[::-1]:
        if call.kwargs.get("embed") is not None:
            return call.kwargs["embed"]
    return None


async def test_registry_and_storage():
    from bot import registry, shortcuts as sc
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            leaves = registry.leaves(bot.tree)
            # كل أمر وخيار له وصف واضح ضمن حد ديسكورد
            for c in leaves:
                assert 1 <= len(str(c.description)) <= 100, c.qualified_name
                for p in c.parameters:
                    assert str(p.description) not in ("", "…") and len(str(p.description)) <= 100, (c.qualified_name, p.name)
            # التصنيف والصلاحيات المقروءة من الفحوص الفعلية
            ban, typing_, settings_, roulette = (registry.find(bot.tree, n) for n in ("ban", "game typing", "settings set", "game roulette"))
            assert registry.required_perm(ban) == "moderation" and registry.kind_of(ban) == "admin"
            assert registry.required_perm(settings_) == "admin"
            assert registry.required_perm(typing_) is None and registry.kind_of(typing_) == "games"
            assert registry.required_perm(registry.find(bot.tree, "game stop")) == "events"
            assert registry.required_perm(registry.find(bot.tree, "ping")) is None
            assert registry.perm_label(typing_) == "الجميع" and "الإدارة" in registry.perm_label(ban)
            assert [p.name for p in roulette.parameters] == ["mode", "join_seconds", "reward"]
            # أي أمر غير مسموح له باختصار (إدارة البوت/الموسيقى)
            for q in ("panel", "commands", "settings set", "permissions add", "music play"):
                assert not registry.shortcut_support(registry.find(bot.tree, q))[0], q
            assert registry.shortcut_support(ban)[0] and registry.shortcut_support(roulette)[0]
            # الوصف الأصلي محفوظ للاستعادة
            assert "روليت" in registry.default_description("game roulette")

            # تخصيص الوصف يُحفظ ويُطبَّق ثم يعود للأصلي عند الاستعادة
            await db.save_command_config(GUILD, "ping", enabled=True, description="وصف مخصص", options={}, admin_id=1)
            registry.apply_overrides(bot.tree, await db.command_configs(GUILD))
            assert str(registry.find(bot.tree, "ping").description) == "وصف مخصص"
            await db.save_command_config(GUILD, "avatar", enabled=True, description=None,
                                         options={"member": "صاحب الصورة"}, admin_id=1)
            registry.apply_overrides(bot.tree, await db.command_configs(GUILD))
            assert str(registry.find(bot.tree, "avatar").parameters[0].description) == "صاحب الصورة"
            await db.save_command_config(GUILD, "ping", enabled=True, description=None, options={}, admin_id=1)
            await db.save_command_config(GUILD, "avatar", enabled=True, description=None, options={}, admin_id=1)
            registry.apply_overrides(bot.tree, await db.command_configs(GUILD))
            assert str(registry.find(bot.tree, "ping").description) == registry.default_description("ping")
            assert (await db.command_configs(GUILD)) == {}          # الإعداد الافتراضي لا يترك صفوفًا
            for bad in ("", "x" * 101, "bad\x00"):
                try:
                    registry.validate_description(bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass

            # حالة التفعيل والبوابة
            await db.save_command_config(GUILD, "kick", enabled=False, description=None, options={}, admin_id=1)
            assert await registry.is_disabled(bot, GUILD, "kick") and not await registry.is_disabled(bot, 2, "kick")
            await db.save_command_config(GUILD, "panel", enabled=False, description=None, options={}, admin_id=1)
            assert not await registry.is_disabled(bot, GUILD, "panel")          # الأوامر المحمية لا تُعطَّل أبدًا
            import main
            fake = SimpleNamespace(command=registry.find(bot.tree, "kick"), guild_id=GUILD)
            try:
                await bot.tree.interaction_check(fake)
                raise AssertionError("disabled command passed the gate")
            except main.CommandDisabled:
                pass
            fake.guild_id = 2
            assert await bot.tree.interaction_check(fake)                        # سيرفر آخر غير متأثر

            # الاختصارات: تفرّد، إعادة تسمية، حذف
            assert await db.add_shortcut(GUILD, "bn", "ban", 1) is True
            assert await db.add_shortcut(GUILD, "bn", "kick", 1) is False
            res = await asyncio.gather(*[db.add_shortcut(GUILD, "same", "ban", 1) for _ in range(10)])
            assert sum(res) == 1
            assert await db.rename_shortcut(GUILD, "bn", "b2") and not await db.rename_shortcut(GUILD, "nope", "x")
            assert not await db.rename_shortcut(GUILD, "b2", "same")             # الاسم المستخدم يمنع إعادة التسمية
            assert (await db.shortcuts(GUILD)) == {"b2": "ban", "same": "ban"}
            assert (await db.shortcuts(2)) == {}                                 # لا تسرب بين السيرفرات
            assert await db.delete_shortcut(GUILD, "same") and not await db.delete_shortcut(GUILD, "same")

            # قواعد التحقق من اسم الاختصار
            tree, table = bot.tree, await db.shortcuts(GUILD)
            for good in ("ban", "حظر", "b_3", "!ban"):
                assert sc.validate_shortcut(tree, table, good, ban) in ("ban", "حظر", "b_3")
            for bad, target in (("Bad Name", ban), ("", ban), ("x" * 21, ban), ("b2", ban),   # صيغة/تكرار
                                ("kick", ban), ("ping", ban), ("game-math", ban),            # يطابق اسم أمر آخر
                                ("zz", registry.find(tree, "panel"))):                       # أمر غير مسموح
                try:
                    sc.validate_shortcut(tree, table, bad, target)
                    raise AssertionError(bad)
                except ValueError:
                    pass
            assert sc.validate_shortcut(tree, table, "b2", ban, replacing="b2") == "b2"
            # إعدادات البادئة
            from bot.settings_schema import parse_value, SETTINGS_BY_KEY
            for good in ("!", ".", "$$", "؟"):
                assert parse_value(SETTINGS_BY_KEY["shortcut_prefix"], good) == good
            for bad in ("", "ab", "/", "@", "a!", "!!!!", " ", "<"):
                try:
                    parse_value(SETTINGS_BY_KEY["shortcut_prefix"], bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass
            # بقاء كل شيء بعد إعادة التشغيل
            await db.close()
            db2 = await temp_db(d)
            assert (await db2.command_configs(GUILD))["kick"]["enabled"] is False
            assert (await db2.shortcuts(GUILD)) == {"b2": "ban"}
            await db2.close()
        finally:
            if db.conn:
                await db.close()
    print("OK commands registry: descriptions, perms read from real checks, overrides/reset, gate, "
          "shortcut rules, uniqueness under concurrency, persistence")


async def test_shortcut_parsing_and_execution():
    from bot import registry, shortcuts as sc
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            tree = bot.tree
            target_member = fake_member(UID + 1)
            members = {UID + 1: target_member}
            ban, mute, clear_, roulette, bal = (registry.find(tree, n) for n in ("ban", "mute", "clear", "game roulette", "balance"))
            msg = fake_message("", fake_member(1, admin=True), members)

            # --- المحلل
            assert [t for t, _ in sc.tokenize('a "b c" d')] == ["a", "b c", "d"]
            kw = await sc.parse_arguments(bot, msg, ban, f"<@{UID + 1}> 3 spam and more words")
            assert kw["user"] is target_member and kw["delete_days"] == 3 and kw["reason"] == "spam and more words"
            kw = await sc.parse_arguments(bot, msg, ban, f"<@{UID + 1}> spam reason")      # يتخطى delete_days
            assert kw == {"user": target_member, "reason": "spam reason"}, kw
            kw = await sc.parse_arguments(bot, msg, ban, f"{UID + 1}")
            assert kw == {"user": target_member}                                       # الباقي يأخذ الافتراضي
            kw = await sc.parse_arguments(bot, msg, clear_, "٢٥")
            assert kw == {"amount": 25}                                               # أرقام عربية
            kw = await sc.parse_arguments(bot, msg, roulette, "إقصاء 30")
            assert kw == {"mode": "إقصاء", "join_seconds": 30}
            for text, cmd_ in (("", ban), (f"<@{UID + 1}> 9", ban), (f"<@{UID + 1}>", mute), ("abc", clear_),
                               ("0", clear_), ("999", clear_), ("خيار_خاطئ", roulette), ("إقصاء 5", roulette),
                               ("<@999999999999999999>", registry.find(tree, "kick")), ("1 2 3", registry.find(tree, "ping"))):
                try:
                    await sc.parse_arguments(bot, msg, cmd_, text)
                    raise AssertionError((text, cmd_.qualified_name))
                except sc.ShortcutError:
                    pass
            assert "<member>" in sc.usage(registry.find(tree, "kick"), "!", "k") or "member" in sc.usage(registry.find(tree, "kick"), "!", "k")

            # --- اكتشاف الرسالة (لا أوامر بالخطأ من الكلام العادي)
            await db.add_shortcut(GUILD, "bn", "ban", 1)
            await db.add_shortcut(GUILD, "p", "ping", 1)
            await db.add_shortcut(GUILD, "رصيد", "balance", 1)
            normal = fake_member(2)
            for text in ("bn @x", "hello !bn", "!unknown", "!", "!!bn", "", "ban"):
                assert await sc.find_target(bot, fake_message(text, normal, members)) is None, text
            found = await sc.find_target(bot, fake_message("!BN <@1> سبب طويل", normal, members))
            assert found and found[0] == "bn" and found[1].qualified_name == "ban" and found[2] == "<@1> سبب طويل"
            assert (await sc.find_target(bot, fake_message("!رصيد", normal, members)))[1].qualified_name == "balance"
            bot_msg = fake_message("!p", fake_member(77, bot=True), members)
            assert await sc.find_target(bot, bot_msg) is None
            dm = fake_message("!p", normal, members)
            dm.guild = None
            assert await sc.find_target(bot, dm) is None
            await db.set_setting(GUILD, "shortcut_prefix", ".")
            assert await sc.find_target(bot, fake_message("!p", normal, members)) is None
            assert (await sc.find_target(bot, fake_message(".p", normal, members)))[0] == "p"
            await db.set_setting(GUILD, "shortcuts_enabled", False)
            assert await sc.find_target(bot, fake_message(".p", normal, members)) is None
            await db.reset_setting(GUILD, "shortcuts_enabled")
            await db.reset_setting(GUILD, "shortcut_prefix")

            # --- تنفيذ فعلي: أمر عام يعمل
            m = fake_message("!p", normal, members)
            await sc.run(bot, m, "p", registry.find(tree, "ping"), "", "!")
            emb = last_embed(m)
            assert emb is not None and "Pong" in emb.title

            # --- أمر إداري: المستخدم العادي ممنوع والأمر الأصلي لا يُنفَّذ إطلاقًا
            calls = []

            async def recorder(binding, interaction, **kw):
                calls.append((interaction.user.id, kw))

            ban._callback = recorder
            m = fake_message(f"!bn <@{UID + 1}> test", normal, members)
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}> test", "!")
            assert not calls and "صلاحية" in last_embed(m).description
            # صاحب رتبة الإدارة المخصصة يمرّ، والعضو بصلاحية ديسكورد العامة فقط (بعد تحديد رتب) لا يمرّ
            await db.set_perm_roles(GUILD, "moderation", [700])
            mod = fake_member(3, role_ids=(700,))
            m = fake_message(f"!bn <@{UID + 1}> test", mod, members)
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}> test", "!")
            assert calls == [(3, {"user": target_member, "reason": "test"})], (calls, last_embed(m) and last_embed(m).to_dict())
            calls.clear()
            perm_only = fake_member(4, moderate_members=True)
            m = fake_message(f"!bn <@{UID + 1}>", perm_only, members)
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}>", "!")
            assert not calls
            # البوت لا يملك الصلاحية المطلوبة (فحص bot_has_permissions الأصلي)
            m = fake_message(f"!bn <@{UID + 1}>", mod, members)
            m.channel.permissions_for = lambda _m: discord.Permissions.none()
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}>", "!")
            assert not calls and last_embed(m) is not None
            # أمر معطّل لا يعمل عبر الاختصار أيضًا (حتى للمسؤول)
            await db.save_command_config(GUILD, "ban", enabled=False, description=None, options={}, admin_id=1)
            m = fake_message(f"!bn <@{UID + 1}>", fake_member(1, admin=True), members)
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}>", "!")
            assert not calls and "معطّل" in last_embed(m).description
            await db.save_command_config(GUILD, "ban", enabled=True, description=None, options={}, admin_id=1)
            # خطأ مدخلات: رسالة توضح الاستخدام ولا ينفذ شيء
            m = fake_message("!bn", mod, members)
            await sc.run(bot, m, "bn", ban, "", "!")
            assert not calls and "طريقة الاستخدام" in last_embed(m).description
            # استثناء داخل الأمر لا يكسر البوت ويرد برسالة
            async def exploding(binding, interaction, **kw):
                raise RuntimeError("boom")

            ban._callback = exploding
            m = fake_message(f"!bn <@{UID + 1}>", mod, members)
            await sc.run(bot, m, "bn", ban, f"<@{UID + 1}>", "!")
            assert "خطأ غير متوقع" in last_embed(m).description

            # --- أمر حقيقي بالكامل: الرصيد يقرأ قاعدة البيانات عبر الاختصار
            await db.admin_adjust(GUILD, UID + 1, 250, 1, "t")
            await db.set_setting(GUILD, "info_cards", False)       # هنا نختبر منطق الاختصار؛ البطاقات لها اختبارها
            m = fake_message(f"!رصيد <@{UID + 1}>", normal, members)
            target_member.display_avatar = SimpleNamespace(url="https://example.com/a.png")
            target_member.display_name = "target"
            await sc.run(bot, m, "رصيد", bal, f"<@{UID + 1}>", "!")
            emb = last_embed(m)
            assert emb is not None and any("250" in str(f.value) for f in emb.fields), emb and emb.to_dict()
        finally:
            await db.close()
    print("OK shortcuts: parsing, prefix safety, same checks as slash (normal/role/perm-only/bot perms/disabled), "
          "usage errors, crash isolation, real command via shortcut")


async def test_commands_panel():
    from bot import registry
    from cogs import cmd_admin as ca
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            admin, normal = fake_member(1, admin=True), fake_member(2)
            view = ca.CommandsView(bot, admin, MagicMock(id=GUILD))

            # كل الأوضاع تُبنى ضمن حدود ديسكورد (صفوف، عناصر، طول الرسالة)
            async def check_layout():
                e = await view.build()
                assert len(view.children) <= 25 and len(e) <= 6000 and len(e.fields) <= 25
                rows = {}
                for ch in view.children:
                    rows[ch.row] = rows.get(ch.row, 0) + (5 if isinstance(ch, discord.ui.Select) else 1)
                assert all(v <= 5 for v in rows.values()), rows
                for ch in view.children:
                    if isinstance(ch, discord.ui.Select):
                        assert 1 <= len(ch.options) <= 25
                return e

            await db.add_shortcut(GUILD, "bn", "ban", 1)
            for kind in ("all", "general", "games", "admin"):
                view.kind, view.mode, view.page = kind, "list", 0
                await check_layout()
            view.query = "ban"
            e = await check_layout()
            assert "/ban" in e.description
            view.query, view.kind = "", "all"
            view.page = 99                       # صفحة خارج النطاق تُصحَّح
            await check_layout()
            assert view.page == max(0, (len(view._filtered()) + ca.PAGE - 1) // ca.PAGE - 1)
            view.current, view.mode = "game roulette", "detail"
            e = await check_layout()
            assert "mode" in str(e.to_dict()) and "join_seconds" in str(e.to_dict())
            for mode in ("pick_option", "shortcuts", "pick_command"):
                view.mode = mode
                await check_layout()
            view.current, view.mode = None, "shortcuts"
            await check_layout()

            def itx(user, guild_id=GUILD):
                i = fake_interaction(user, guild_id)
                return i

            # الأمان: مستخدم آخر / سيرفر آخر / مسؤول فقد صلاحيته
            assert not await view.interaction_check(itx(normal))
            assert not await view.interaction_check(itx(admin, 2))
            demoted = fake_member(1)
            view.mode, view.current = "detail", "kick"
            i = itx(demoted)
            await view.on_action(i, "toggle") if await view.guard(i) else None
            assert (await db.command_configs(GUILD)).get("kick") is None, "demoted admin changed config"
            # نافذة مرسلة من غير مسؤول لا تغيّر شيئًا
            modal = ca.ShortcutModal(view, "kick")
            modal.name._value = "kk"
            await modal.on_submit(itx(normal))
            assert "kk" not in await db.shortcuts(GUILD)

            # تعطيل/تفعيل فعلي + حماية الأوامر الأساسية
            view.mode, view.current = "detail", "kick"
            i = itx(admin)
            await view.on_action(i, "toggle")
            assert (await db.command_configs(GUILD))["kick"]["enabled"] is False
            await view.on_action(itx(admin), "toggle")
            assert "kick" not in await db.command_configs(GUILD)
            view.current = "panel"
            await view.on_action(itx(admin), "toggle")
            assert "panel" not in await db.command_configs(GUILD)

            # تعديل الوصف: معاينة ثم حفظ، والإلغاء لا يحفظ
            view.current, view.mode = "ping", "detail"
            modal = ca.DescModal(view, "ping", None)
            modal.text._value = "قياس سرعة الاستجابة"
            await modal.on_submit(itx(admin))
            assert view.mode == "preview" and view.pending[3] == "قياس سرعة الاستجابة"
            assert "ping" not in await db.command_configs(GUILD)               # لم يُحفظ بعد
            await view.on_action(itx(admin), "cancel_pending")
            assert "ping" not in await db.command_configs(GUILD) and view.mode == "detail"
            modal = ca.DescModal(view, "ping", None)
            modal.text._value = "قياس سرعة الاستجابة"
            await modal.on_submit(itx(admin))
            await view.on_action(itx(admin), "save_pending")
            assert (await db.command_configs(GUILD))["ping"]["description"] == "قياس سرعة الاستجابة"
            assert str(registry.find(bot.tree, "ping").description) == "قياس سرعة الاستجابة" and bot.desc_dirty
            # وصف خيار
            view.current, view.mode = "avatar", "detail"
            modal = ca.DescModal(view, "avatar", "member")
            modal.text._value = "صاحب الصورة المطلوبة"
            await modal.on_submit(itx(admin))
            await view.on_action(itx(admin), "save_pending")
            assert str(registry.find(bot.tree, "avatar").parameters[0].description) == "صاحب الصورة المطلوبة"
            # وصف طويل أو فارغ مرفوض برسالة
            modal = ca.DescModal(view, "avatar", None)
            modal.text._value = "x" * 150
            await modal.on_submit(itx(admin))
            assert view.mode != "preview" or view.pending[3] != "x" * 150
            # الاستعادة تتطلب ضغطتين
            view.current, view.mode = "ping", "detail"
            await view.on_action(itx(admin), "reset")
            assert (await db.command_configs(GUILD)).get("ping") and view.confirm == "reset"
            await view.on_action(itx(admin), "reset")
            assert "ping" not in await db.command_configs(GUILD)
            assert str(registry.find(bot.tree, "ping").description) == registry.default_description("ping")

            # الاختصارات من اللوحة: إضافة، تكرار، تعارض، إعادة تسمية، حذف بضغطتين
            view.current, view.mode = "kick", "detail"
            modal = ca.ShortcutModal(view, "kick")
            modal.name._value = "kk"
            await modal.on_submit(itx(admin))
            assert (await db.shortcuts(GUILD))["kk"] == "kick" and view.mode == "shortcuts"
            for bad in ("kk", "ban", "ping"):
                modal = ca.ShortcutModal(view, "kick")
                modal.name._value = bad
                await modal.on_submit(itx(admin))
            assert (await db.shortcuts(GUILD)) == {"bn": "ban", "kk": "kick"}
            view.sel_shortcut = "kk"
            modal = ca.ShortcutModal(view, "kick", rename="kk")
            modal.name._value = "طرد"
            await modal.on_submit(itx(admin))
            assert (await db.shortcuts(GUILD)) == {"bn": "ban", "طرد": "kick"}
            await view.on_action(itx(admin), "delete_shortcut")
            assert "طرد" in await db.shortcuts(GUILD)                              # الضغطة الأولى لا تحذف
            await view.on_action(itx(admin), "delete_shortcut")
            assert "طرد" not in await db.shortcuts(GUILD)
            # الاقتراحات: تُضيف غير المتعارض فقط، والتشغيل مرة ثانية لا يكرر
            await view.on_action(itx(admin), "suggest")
            first = dict(await db.shortcuts(GUILD))
            assert first["bn"] == "ban" and first.get("lb") == "leaderboard" and first.get("roulette") == "game roulette"
            await view.on_action(itx(admin), "suggest")
            assert dict(await db.shortcuts(GUILD)) == first
            # البادئة وتشغيل الاختصارات
            modal = ca.PrefixModal(view, "!")
            modal.prefix._value = "."
            await modal.on_submit(itx(admin))
            assert (await db.get_settings(GUILD))["shortcut_prefix"] == "."
            modal.prefix._value = "ab"
            await modal.on_submit(itx(admin))
            assert (await db.get_settings(GUILD))["shortcut_prefix"] == "."
            await view.on_action(itx(admin), "toggle_shortcuts")
            assert (await db.get_settings(GUILD))["shortcuts_enabled"] is False
            # بحث ومسح التصفية
            modal = ca.SearchModal(view)
            modal.q._value = "game"
            view.mode, view.kind = "list", "all"
            await modal.on_submit(itx(admin))
            assert view.query == "game" and all("game" in c.qualified_name or "game" in str(c.description).lower()
                                                for c in view._filtered())
            await view.on_action(itx(admin), "clear_filter")
            assert view.query == "" and view.kind == "all"
            # المزامنة: تحترم مهلة الانتظار ولا تُرسل شيئًا للمنصة في الاختبار
            from bot import registry as reg
            reg._last_sync = __import__("time").monotonic()
            view.mode = "list"
            i = itx(admin)
            await view.on_action(i, "sync")
            shown = i.response.edit_message.await_args.kwargs["embed"]
            assert any("انتظر" in str(f.value) for f in shown.fields), shown.to_dict()   # المهلة تمنع الطلب
            # بعد انقضاء المهلة: تُنسخ الأوامر وتُرسل للمنصة مرة واحدة (محاكاة ديسكورد) وتُصفَّر علامة «غير مطبّق»
            reg._last_sync = 0.0
            bot.tree.sync = AsyncMock(return_value=[0] * 33)
            bot.desc_dirty = True
            os.environ["GUILD_ID"] = str(GUILD)
            try:
                i = itx(admin)
                await view.on_action(i, "sync")
            finally:
                os.environ.pop("GUILD_ID", None)
            assert bot.tree.sync.await_count == 1 and bot.desc_dirty is False
            assert any(f.name.startswith("✅") for f in i.edit_original_response.await_args.kwargs["embed"].fields)
            await view.on_action(itx(admin), "sync")                                 # فورًا: مهلة جديدة
            assert bot.tree.sync.await_count == 1
        finally:
            await db.close()
    print("OK /commands panel: layout limits, owner/guild/server-side admin lock, toggle+protection, preview-then-save, "
          "reset confirm, shortcuts add/duplicate/conflict/rename/delete, suggestions, prefix, search, sync cooldown")


# ====================================================================== المتجر والبطاقات والصوت والموسيقى
async def test_shop_store_and_panel():
    from bot import cards, images
    from cogs import panel as pm
    from cogs import shop as shop_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            # ---- حقول جديدة + حذف ناعم يحفظ المشتريات
            a = await db.add_shop_item(GUILD, "VIP", "وصف", 100, "custom", None, 5, 0, True,
                                       image_url="https://example.com/a.png")
            b = await db.add_shop_item(GUILD, "Pass", "", 50, "custom", None, -1, 0, False)
            assert (await db.shop_item(GUILD, a))["image_url"] == "https://example.com/a.png"
            assert (await db.shop_item(GUILD, b))["image_url"] is None and (await db.shop_item(GUILD, b))["deleted"] == 0
            await db.admin_set(GUILD, 7, 1000, 1, "t")
            pid, _ = await db.purchase(GUILD, 7, a)
            assert await db.delete_shop_item(GUILD, a) and not await db.delete_shop_item(GUILD, a)
            assert [r["id"] for r in await db.shop_items(GUILD, include_inactive=True)] == [b]
            try:
                await db.purchase(GUILD, 7, a)
                raise AssertionError("deleted item purchasable")
            except LimitReached:
                pass
            assert (await db.refund(GUILD, pid, 1))["price"] == 100        # الاسترداد يعمل بعد حذف المنتج
            assert await db.balance(GUILD, 7) == 1000
            await db.update_shop_item(GUILD, b, image_url="")
            assert (await db.shop_item(GUILD, b))["image_url"] == ""

            # ---- شراء متزامن: لا خصم مزدوج ولا تجاوز للمخزون
            c = await db.add_shop_item(GUILD, "Limited", "", 30, "custom", None, 2, 0, False)
            await db.admin_set(GUILD, 8, 1000, 1, "t")
            res = await asyncio.gather(*[db.purchase(GUILD, 8, c) for _ in range(10)], return_exceptions=True)
            ok = [r for r in res if not isinstance(r, Exception)]
            assert len(ok) == 2 and await db.balance(GUILD, 8) == 1000 - 60 and (await db.shop_item(GUILD, c))["stock"] == 0

            # ---- التحقق من رابط الصورة
            assert images.validate_url("") == "" and images.validate_url(" https://cdn.example.com/p.png ").startswith("https")
            for bad in ("http://example.com/a.png", "ftp://x/a", "https://127.0.0.1/a.png", "https://10.0.0.5/a.png",
                        "https://user:pw@example.com/a.png", "https://[::1]/a.png", "javascript:alert(1)", "x" * 500):
                try:
                    images.validate_url(bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass
            assert await images.fetch("https://127.0.0.1/a.png") is None             # SSRF محظور دون طلب شبكة
            assert await images.fetch("http://example.com/a.png") is None
            assert await images.fetch("") is None

            # ---- المتجر المرئي: يُرسم، ويرجع للنص عند فشل الرسم
            admin, normal = fake_member(1, admin=True), fake_member(2)
            guild = MagicMock()
            guild.id, guild.name = GUILD, "Test"
            e, file, chunk, pages, page = await shop_mod.render_store(bot, guild, normal, 0)
            assert file is not None and file.filename == "store.png" and len(chunk) == 2 and pages == 1
            real = cards.render_store
            cards.render_store = lambda **k: (_ for _ in ()).throw(RuntimeError("x"))
            try:
                e, file, chunk, pages, page = await shop_mod.render_store(bot, guild, normal, 0)
                assert file is None and "Pass" in e.description                        # بديل نصي
            finally:
                cards.render_store = real
            assert await db.delete_shop_item(GUILD, c) and await db.delete_shop_item(GUILD, b)
            e, file, chunk, *_ = await shop_mod.render_store(bot, guild, normal, 0)
            assert file is not None and not chunk                                      # متجر فارغ لا ينهار
            await db.add_shop_item(GUILD, "Again", "", 10, "custom", None, -1, 0, False)

            # ---- زر إدارة المتجر: لا يعمل لغير المخوّلين (فحص من الخادم)
            view = shop_mod.StoreView(bot, normal, guild, can_manage=True)           # حتى لو ظهر الزر لسبب ما
            btn = shop_mod._StoreBtn("manage", "x", "⚙️", row=1)
            btn._view = view
            i = fake_interaction(normal)
            i.guild = guild
            await btn.callback(i)
            assert "لأصحاب صلاحية المتجر" in str(i.response.send_message.await_args.kwargs["embed"].description)
            btn2 = shop_mod._StoreBtn("manage", "x", "⚙️", row=1)
            btn2._view = shop_mod.StoreView(bot, admin, guild, can_manage=True)
            i2 = fake_interaction(admin)
            i2.guild = guild
            await btn2.callback(i2)
            assert i2.response.send_message.await_args.kwargs.get("ephemeral") is True       # لوحة مخفية للمسؤول
            # غير صاحب المتجر لا يستطيع التحكم بأزراره
            assert not await view.interaction_check(fake_interaction(admin))

            # ---- لوحة المتجر: إضافة بصورة، تعديل، مخزون، حذف بتأكيد، ومنع غير المخوّل
            pview = pm.PanelView(bot, admin, MagicMock(id=GUILD))
            pview.section = pm.SECTIONS_BY_KEY["shop"]
            item_id = None

            def itx(u=admin):
                return fake_interaction(u)

            modal = pm.ItemModal(pview, kind="custom")
            modal.name._value, modal.price._value = "Gift", "250"
            modal.desc._value, modal.image._value, modal.stock._value = "هدية", "https://cdn.example.com/g.png", "7"
            await modal.on_submit(itx())
            row = [r for r in await db.shop_items(GUILD, include_inactive=True) if r["name"] == "Gift"][0]
            item_id = row["id"]
            assert (row["price"], row["stock"], row["image_url"]) == (250, 7, "https://cdn.example.com/g.png")
            # رابط غير صالح يُرفض برسالة ولا يحفظ
            bad = pm.ItemModal(pview, kind="custom")
            bad.name._value, bad.price._value, bad.image._value, bad.stock._value = "Bad", "5", "http://x.com/a.png", "1"
            await bad.on_submit(itx())
            assert not [r for r in await db.shop_items(GUILD, include_inactive=True) if r["name"] == "Bad"]
            # سعر غير رقمي
            bad2 = pm.ItemModal(pview, kind="custom")
            bad2.name._value, bad2.price._value, bad2.stock._value = "Bad2", "abc", "1"
            await bad2.on_submit(itx())
            assert not [r for r in await db.shop_items(GUILD, include_inactive=True) if r["name"] == "Bad2"]
            # تعديل السعر والصورة
            pview.mode, pview.target = "item", item_id
            item = await db.shop_item(GUILD, item_id)
            em = pm.ItemModal(pview, kind="custom", item=item)
            em.price._value, em.image._value = "300", ""
            await em.on_submit(itx())
            row = await db.shop_item(GUILD, item_id)
            assert row["price"] == 300 and row["image_url"] == "" and row["stock"] == 7
            # المخزون
            sm = pm.StockModal(pview, row)
            sm.stock._value = "-1"
            await sm.on_submit(itx())
            assert (await db.shop_item(GUILD, item_id))["stock"] == -1
            sm.stock._value = "abc"
            await sm.on_submit(itx())
            assert (await db.shop_item(GUILD, item_id))["stock"] == -1
            # الحذف يتطلب ضغطتين، ومستخدم عادي لا يحذف
            await pview.on_action(itx(normal), "delete_item")
            assert (await db.shop_item(GUILD, item_id))["deleted"] == 0
            await pview.on_action(itx(), "delete_item")
            assert (await db.shop_item(GUILD, item_id))["deleted"] == 0 and pview.confirm_delete == item_id
            await pview.on_action(itx(), "delete_item")
            assert (await db.shop_item(GUILD, item_id))["deleted"] == 1
            # عرض اللوحة بعد كل ذلك ضمن حدود ديسكورد (مع صفحات للمنتجات الكثيرة)
            for n in range(20):
                await db.add_shop_item(GUILD, f"P{n}", "", n, "custom", None, -1, 0, False)
            pview.mode, pview.page = None, 1
            em_ = await pview.build(admin)
            assert len(pview.children) <= 25 and len(em_) <= 6000
        finally:
            await db.close()
    print("OK shop: soft delete keeps refunds, image/stock fields, concurrent purchase safety, SSRF-safe image URLs, "
          "store card + text fallback, admin button server-side check, panel add/edit/stock/delete-confirm")


def test_info_cards():
    from PIL import Image
    from bot import cards
    import io as _io
    assert cards.available()
    px_ = lambda b: Image.open(_io.BytesIO(b)).size
    names = ["محمد عبدالله الطويل جدًا جدًا", "A" * 60, "🔥🔥", "سارة", ""]
    for n in names:
        assert px_(cards.render_balance(name=n, avatar=None, currency="نقطة", balance=0, rank=1, total_earned=0,
                                        games_won=0, earned_today=0, daily_cap=0, guild_name=n)) == (1280, 720)
        assert px_(cards.render_balance(name=n, avatar=b"junk", currency="x" * 30, balance=10 ** 12, rank=10 ** 5,
                                        total_earned=10 ** 12, games_won=10 ** 6, earned_today=900, daily_cap=500,
                                        guild_name="s")) == (1280, 720)
        assert px_(cards.render_profile(name=n, tag="@u", avatar=None, balance=5, currency="نقطة", rank=2, games_won=1,
                                        total_earned=9, warnings=3, roles=12, joined="2025-01-01", created="2020-01-01",
                                        top_role=n, guild_name=n)) == (1280, 720)
    rows = [dict(rank=i + 1, user_id=i, name=names[i % len(names)], balance=10 ** (i % 9)) for i in range(10)]
    for k in (0, 1, 7, 10):
        assert px_(cards.render_leaderboard(rows=rows[:k], avatars={}, viewer_id=3, viewer_rank=99, viewer_balance=1,
                                            currency="نقطة", guild_name="g", page=1, pages=1)) == (1280, 720)
    items = [dict(id=i, name=names[i % 5], price=10 ** i, kind="role" if i % 2 else "custom", stock=[-1, 0, 5][i % 3],
                  duration_hours=[0, 24, 5][i % 3]) for i in range(6)]
    for k in (0, 1, 6):
        assert px_(cards.render_store(items=items[:k], images={0: b"junk", 1: None}, balance=1, currency="نقطة",
                                      guild_name="g", page=1, pages=1)) == (1280, 720)
    assert px_(cards.render_nowplaying(title="x" * 200, requester=names[0], duration="مباشر", thumbnail=b"junk",
                                       queue_len=0, volume=150, loop=True, guild_name="g")) == (1280, 720)
    print("OK info cards: balance/profile/leaderboard/store/now-playing at 1280x720 with long/odd/empty data and bad images")


async def test_info_commands_use_cards_with_fallback():
    from bot import cards
    from cogs import economy as eco
    from cogs import info as info_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            await db.admin_set(GUILD, 5, 750, 1, "t")
            member = fake_member(5)
            member.display_name = "سارة"
            member.display_avatar = SimpleNamespace(url="u", with_size=lambda n: SimpleNamespace(
                read=AsyncMock(return_value=b"junk")))
            member.created_at = member.joined_at = discord.utils.utcnow()
            member.name = "sara"
            member.roles = [SimpleNamespace(id=1, name="@everyone", is_default=lambda: True)]
            cog = bot.get_cog("Economy")

            def inter():
                i = fake_interaction(member)
                i.guild.name = "Test"
                i.guild.icon = None
                i.followup = MagicMock()
                i.followup.send = AsyncMock()
                return i

            # بطاقة: يُرسل ملف صورة ببيانات الرصيد الحقيقية
            i = inter()
            await cog.balance.callback(cog, i, member=None)
            sent = i.followup.send.await_args.kwargs
            assert sent["file"].filename == "balance.png"
            # فشل الرسم: بديل نصي بنفس البيانات الحقيقية
            real = cards.render_balance
            cards.render_balance = lambda **k: (_ for _ in ()).throw(RuntimeError("x"))
            try:
                i = inter()
                await cog.balance.callback(cog, i, member=None)
                emb = i.followup.send.await_args.kwargs["embed"]
                assert any("750" in str(f.value) for f in emb.fields)
            finally:
                cards.render_balance = real
            # الإعداد معطّل: نص مباشرة
            await db.set_setting(GUILD, "info_cards", False)
            i = fake_interaction(member)
            i.guild.icon = None
            await cog.balance.callback(cog, i, member=None)
            assert "file" not in i.response.send_message.await_args.kwargs
            await db.reset_setting(GUILD, "info_cards")
            # المتصدرون: أول صفحة تُرسل كصورة
            i = inter()
            await cog.leaderboard.callback(cog, i)
            assert i.followup.send.await_args.kwargs["file"].filename == "leaderboard.png"
        finally:
            await db.close()
    print("OK balance/leaderboard commands: card with real data, text fallback on render failure or setting off")


async def test_fast_game_and_trigger():
    import datetime
    import cogs.games as games_mod
    from bot import registry
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            cog = bot.get_cog("Games")
            old = discord.utils.utcnow() - datetime.timedelta(days=400)
            assert cog.answer_key("المشتري") == cog.answer_key("مُشتري") == cog.answer_key(" المشتري ")
            assert cog.answer_key("الأرض") == cog.answer_key("ارض") and cog.answer_key("7") == "7"

            calls = []

            async def recorder(interaction, reward):
                calls.append((interaction.user.id, interaction.channel_id, reward))

            real = cog._run_fast
            cog._run_fast = recorder
            member = fake_member(5)
            member.created_at = member.joined_at = old

            def msg(text, author=member):
                m = fake_message(text, author)
                m.guild.id = GUILD
                return m

            for text in ("-اسرع", "  -اسرع  ", "-أسرع"):
                await cog.on_message(msg(text))
            assert len(calls) == 3
            calls.clear()
            for text in ("اسرع", "-اسرع الان", "كلام -اسرع", "-", "", "-سريع", "/game fast"):
                await cog.on_message(msg(text))
            await cog.on_message(msg("-اسرع", fake_member(9, bot=True)))
            dm = msg("-اسرع")
            dm.guild = None
            await cog.on_message(dm)
            assert not calls, calls
            await db.set_setting(GUILD, "fast_trigger", ".بدء")
            await cog.on_message(msg("-اسرع"))
            await cog.on_message(msg(".بدء"))
            assert len(calls) == 1
            await db.reset_setting(GUILD, "fast_trigger")
            await db.save_command_config(GUILD, "game fast", enabled=False, description=None, options={}, admin_id=1)
            await cog.on_message(msg("-اسرع"))
            assert len(calls) == 1                         # معطّل من لوحة الأوامر
            await db.save_command_config(GUILD, "game fast", enabled=True, description=None, options={}, admin_id=1)
            from bot.settings_schema import SETTINGS_BY_KEY, parse_value
            for ok in ("-اسرع", ".go", "؟سؤال"):
                assert parse_value(SETTINGS_BY_KEY["fast_trigger"], ok) == ok
            for bad in ("اسرع", "-", "- x", "/x", "@x", "1ab"):
                try:
                    parse_value(SETTINGS_BY_KEY["fast_trigger"], bad)
                    raise AssertionError(bad)
                except ValueError:
                    pass
            cog._run_fast = real

            # ---- جولة كاملة عبر الرسالة: الفائز الأول فقط يُصرف له الجائزة مرة واحدة
            question, answer, _w = games_mod.QUESTIONS[2]          # المشتري
            winners = []
            # load_extension يعيد تنفيذ الوحدة كنسخة جديدة، فنعدّل وحدة الكوغ الفعلية لا النسخة المستوردة سابقًا
            sys.modules[type(cog).__module__]._rng = SimpleNamespace(choice=lambda seq: (question, answer, []),
                                                                     randint=lambda a, b: a)

            class Arrivals:
                def __init__(self):
                    self.queue = []

                async def wait_for(self, _event, check=None, timeout=None):
                    if self.queue:
                        await asyncio.sleep(1.3)   # زمن بشري واقعي: الأسرع من الحد الأدنى يُرفض عمدًا (مضاد للغش)
                    for author, text in list(self.queue):
                        m = fake_message(text, author)
                        m.channel.id = 5
                        m.reply = AsyncMock(return_value=MagicMock())
                        m.add_reaction = AsyncMock()
                        if check(m):
                            self.queue.remove((author, text))
                            return m
                    raise asyncio.TimeoutError

            arr = Arrivals()
            bot.wait_for = arr.wait_for
            w1, w2 = fake_member(21), fake_member(22)
            for w in (w1, w2):
                w.created_at = w.joined_at = old
                w.display_avatar = SimpleNamespace(url="u")
            arr.queue = [(w1, "خطأ"), (w2, "المشتري"), (w1, "مشتري")]      # الأول صحيح هو w2
            m0 = fake_message("-اسرع", fake_member(20))
            m0.channel.id = 5
            inter = games_mod.TextInteraction(bot, m0, None)
            before = {u: await db.balance(GUILD, u) for u in (21, 22)}
            await cog._run_fast(inter, None)
            dbg = (before, {u: await db.balance(GUILD, u) for u in (21, 22)},
                   [str(c.kwargs.get("embed") and c.kwargs["embed"].to_dict().get("title")) for c in
                    m0.reply.await_args_list + m0.channel.send.await_args_list])
            assert await db.balance(GUILD, 22) - before[22] == 20 and await db.balance(GUILD, 21) == before[21], dbg
            assert 5 not in cog.active
            # جولة ثانية: إجابة خاطئة معروفة (زحل) تُرفض، وإجابة بديلة معتمدة (جوبيتر) بخطأ إملائي بسيط تُقبل
            sys.modules[type(cog).__module__]._rng = SimpleNamespace(
                choice=lambda seq: (question, "المشتري|جوبيتر", ["زحل", "الأرض", "نبتون"]), randint=lambda a, b: a)
            cog.last_game.clear()
            arr.queue = [(w1, "زحل"), (w1, "الارض"), (w2, "جوبيتير")]
            b1, b2 = await db.balance(GUILD, 21), await db.balance(GUILD, 22)
            await cog._run_fast(games_mod.TextInteraction(bot, m0, None), None)
            assert await db.balance(GUILD, 21) == b1 and await db.balance(GUILD, 22) - b2 == 20
            # لا إجابات: انتهاء بلا جائزة
            arr.queue = []
            await db.set_setting(GUILD, "fast_seconds", 8)
            cog.last_game.clear()
            await cog._run_fast(games_mod.TextInteraction(bot, m0, None), None)
        finally:
            await db.close()
    print("OK fast game: -trigger exact match only (bots/DMs/words ignored), custom trigger, disabled via /commands, "
          "first correct answer wins once")


async def test_voice_home_and_music():
    import time as _t
    from bot import voice_home
    from cogs import music as music_mod
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            vh = bot.get_cog("VoiceHome")
            assert [voice_home.backoff_for(i) for i in (1, 2, 3, 4, 5, 6, 7, 50)] == [5, 15, 30, 60, 120, 300, 300, 300]

            def mk_channel(cid=900, perms=None):
                ch = MagicMock(spec=discord.VoiceChannel)
                ch.id, ch.name = cid, "home"
                ch.guild = MagicMock()
                ch.permissions_for = lambda _m: perms or discord.Permissions.all()
                ch.connect = AsyncMock()
                return ch

            home = mk_channel()
            guild = MagicMock()
            guild.id = GUILD
            guild.get_channel = lambda cid: home if cid == 900 else None
            guild.voice_client = None
            home.guild = guild
            # بلا إعداد: لا اتصال
            await vh.ensure(guild)
            home.connect.assert_not_awaited()
            # مع الإعداد: يتصل مرة واحدة
            await db.set_setting(GUILD, "voice_home_channel", 900)
            await vh.ensure(guild)
            assert home.connect.await_count == 1
            # فشل متكرر: تصاعد الانتظار وعدم المحاولة قبل موعدها (لا حلقة اتصال)
            home.connect = AsyncMock(side_effect=asyncio.TimeoutError)
            st = vh._st(GUILD)
            st.next_try, st.failures = 0.0, 0
            await vh.ensure(guild)
            assert st.failures == 1 and home.connect.await_count == 1
            for _ in range(5):
                await vh.ensure(guild)
            assert home.connect.await_count == 1                         # ما زال ضمن مهلة الانتظار
            st.next_try = 0.0
            await vh.ensure(guild)
            assert st.failures == 2 and st.next_try - _t.monotonic() > 10   # 15 ثانية
            # نجاح بعد الفشل يُصفّر العداد
            home.connect = AsyncMock()
            st.next_try = 0.0
            await vh.ensure(guild)
            assert st.failures == 0
            # صلاحيات ناقصة: لا محاولة اتصال وتُسجَّل
            home2 = mk_channel(perms=discord.Permissions(view_channel=True))
            guild.get_channel = lambda cid: home2
            st.next_try = 0.0
            await vh.ensure(guild)
            home2.connect.assert_not_awaited() and None
            assert st.failures >= 1
            # متصل بروم آخر: يعود للمنزل
            guild.get_channel = lambda cid: home
            vc = MagicMock()
            vc.is_connected = lambda: True
            vc.channel = SimpleNamespace(id=555, name="other")
            vc.move_to = AsyncMock()
            guild.voice_client = vc
            st.next_try = 0.0
            await vh.ensure(guild)
            vc.move_to.assert_awaited_once_with(home)
            # متصل بالمنزل فعلًا: لا شيء
            vc.channel = SimpleNamespace(id=900, name="home")
            vc.move_to.reset_mock()
            await vh.ensure(guild)
            vc.move_to.assert_not_awaited()

            # ---- الموسيقى: رسائل أخطاء مفهومة
            ex = music_mod.explain_error
            assert "لم أجد" in ex(LookupError()) and "وقتًا" in ex(asyncio.TimeoutError())
            assert "خاص" in ex(Exception("Private video")) and "تسجيل دخول" in ex(Exception("Sign in to confirm you are not a bot"))
            assert "غير متاح" in ex(Exception("Video unavailable")) and "غير مدعوم" in ex(Exception("Unsupported URL: x"))
            assert "شبكة" in ex(Exception("<urlopen error getaddrinfo failed>")) and "تعذّر جلب" in ex(Exception("???"))
            # fetch_track: محاولة ثانية للأعطال العابرة ثم نجاح
            attempts = []

            def flaky(query, download=False):
                attempts.append(1)
                if len(attempts) == 1:
                    raise music_mod.yt_dlp.utils.DownloadError("connection timed out")
                return {"title": "T", "webpage_url": "u", "url": "s", "duration": 65, "thumbnail": None}

            real = music_mod.ytdl.extract_info
            music_mod.ytdl.extract_info = flaky
            try:
                tr = await music_mod.fetch_track("q", fake_member(1))
                assert tr.title == "T" and len(attempts) == 2 and tr.length == "1:05"
                attempts.clear()
                music_mod.ytdl.extract_info = lambda q, download=False: (_ for _ in ()).throw(
                    music_mod.yt_dlp.utils.DownloadError("Video unavailable"))
                try:
                    await music_mod.fetch_track("q", fake_member(1))
                    raise AssertionError
                except music_mod.yt_dlp.utils.DownloadError:
                    pass
                music_mod.ytdl.extract_info = lambda q, download=False: {"entries": []}
                try:
                    await music_mod.fetch_track("q", fake_member(1))
                    raise AssertionError
                except LookupError:
                    pass
            finally:
                music_mod.ytdl.extract_info = real

            # ---- /play: التحقق من الروم والصلاحيات وFFmpeg وفشل البحث وتحمّل النجاح
            music = bot.get_cog("Music")
            mm = sys.modules[type(music).__module__]    # الوحدة الفعلية للكوغ (load_extension يعيد تنفيذها)
            user = fake_member(30)
            gch = MagicMock(spec=discord.VoiceChannel)
            gch.id = 777
            gch.permissions_for = lambda _m: discord.Permissions.all()
            gch.connect = AsyncMock()
            gch.mention = "<#777>"

            def pi(voice_ch):
                i = fake_interaction(user)
                i.guild = MagicMock()
                i.guild.id = GUILD
                i.guild.voice_client = None
                i.guild.me = MagicMock()
                i.user = user
                user.voice = SimpleNamespace(channel=voice_ch) if voice_ch else None
                i.channel = MagicMock()
                i.followup = MagicMock()
                i.followup.send = AsyncMock()
                return i

            def last(i):
                kw = (i.followup.send.await_args or i.response.send_message.await_args).kwargs
                return kw["embed"].description or kw["embed"].title

            await db.reset_setting(GUILD, "voice_home_channel")
            os.environ.pop("VOICE_CHANNEL_ID", None)
            i = pi(None)
            await music._play(i, "x")
            assert "ادخل روم صوتي" in last(i)
            import shutil as _sh
            real_which = _sh.which
            mm.shutil.which = lambda *_: None
            try:
                i = pi(gch)
                await music._play(i, "x")
                assert "FFmpeg" in last(i)
            finally:
                mm.shutil.which = real_which
            real_fetch = mm.fetch_track
            mm.fetch_track = AsyncMock(side_effect=Exception("Video unavailable"))
            try:
                i = pi(gch)
                await music._play(i, "x")
                assert "غير متاح" in last(i) and gch.connect.await_count == 0   # لا اتصال قبل نجاح البحث
            finally:
                mm.fetch_track = real_fetch
            mm.fetch_track = AsyncMock(return_value=mm.Track("Song", "u", "s", 100, None, user, 0.0))
            gp = music.get_player
            try:
                i = pi(gch)
                await music._play(i, "x")
                assert gch.connect.await_count == 1
                assert music.players[GUILD].queue and music.players[GUILD].queue[0].title == "Song"
            finally:
                mm.fetch_track = real_fetch
                for pl in list(music.players.values()):
                    pl.task.cancel()
                music.players.clear()
            # مع الاتصال الدائم: المستخدم يجب أن يكون في روم المنزل
            await db.set_setting(GUILD, "voice_home_channel", 777)
            i = pi(MagicMock(spec=discord.VoiceChannel))
            i.guild.get_channel = lambda cid: gch
            await music._play(i, "x")
            assert "مثبّت" in last(i)
        finally:
            await db.close()
    print("OK voice 24/7: home channel logic, escalating backoff w/o reconnect loop, move-back, missing perms; "
          "music: clear errors, retry, no connect before search success, home-channel rule")


async def test_duplicate_command_cleanup():
    import main
    bot = main.ServerBot()
    bot.http = SimpleNamespace(bulk_upsert_global_commands=AsyncMock())
    bot._connection = SimpleNamespace(application_id=42, user=None)
    bot.tree.fetch_commands = AsyncMock(return_value=[SimpleNamespace(name="ban"), SimpleNamespace(name="kick")])
    os.environ.pop("KEEP_GLOBAL_COMMANDS", None)
    await bot._remove_stale_global_commands()
    bot.http.bulk_upsert_global_commands.assert_awaited_once_with(42, [])
    bot.http.bulk_upsert_global_commands.reset_mock()
    bot.tree.fetch_commands = AsyncMock(return_value=[])
    await bot._remove_stale_global_commands()                      # لا قديم: لا حذف
    bot.http.bulk_upsert_global_commands.assert_not_awaited()
    bot.tree.fetch_commands = AsyncMock(return_value=[SimpleNamespace(name="ban")])
    os.environ["KEEP_GLOBAL_COMMANDS"] = "1"
    try:
        await bot._remove_stale_global_commands()
        bot.http.bulk_upsert_global_commands.assert_not_awaited()  # الإبقاء بطلب صريح
    finally:
        os.environ.pop("KEEP_GLOBAL_COMMANDS", None)
    print("OK duplicate commands: stale global copies removed only when present, opt-out respected")


def test_visual_sizes_16x9():
    from bot import gamecards as gc, wheel
    from PIL import Image
    import io as _io
    assert (gc.W, gc.H) == (1280, 720) and abs(gc.W / gc.H - 16 / 9) < 1e-6
    assert (wheel.CW, wheel.CH) == (960, 540)
    names = ["أحمد", "Sara", "محمد عبدالله", "Noor", "خالد", "Lina", "عمر", "يوسف"]
    png = wheel.render_result(names, 2, wheel.stop_angle(2, 8))
    assert Image.open(_io.BytesIO(png)).size == (960, 540)
    sp = wheel.render_spin(names, 2, wheel.stop_angle(2, 8), frames=30)
    im = Image.open(_io.BytesIO(sp.gif))
    assert im.size[0] / im.size[1] > 1.7 and len(sp.gif) < wheel.MAX_GIF_BYTES * 1.2
    # عدد كبير من المشاركين: الأرقام على العجلة والأسماء في القائمة
    big = [f"لاعب {i}" for i in range(50)]
    png = wheel.render_result(big, 49, wheel.stop_angle(49, 50))
    assert Image.open(_io.BytesIO(png)).size == (960, 540)
    print("OK visuals: game cards 1280x720 (16:9), roulette 960x540 PNG / ~16:9 GIF, 50 players")


# ====================================================================== المتجر: شراء الرتب والإشعارات
class FakeRole:
    def __init__(self, rid, position=1, managed=False, default=False):
        self.id, self.position, self.managed, self._default = rid, position, managed, default
        self.mention, self.name = f"<@&{rid}>", f"role{rid}"

    def is_default(self):
        return self._default

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position


def buyer(uid, balance_roles=()):
    m = MagicMock(spec=discord.Member)
    m.id, m.bot, m.mention, m.display_name = uid, False, f"<@{uid}>", f"user{uid}"
    m.roles = list(balance_roles)
    m.display_avatar = SimpleNamespace(url="https://example.com/a.png")
    return m


def shop_guild(top_position=10, manage_roles=True, roles=()):
    g = MagicMock()
    g.id, g.name = GUILD, "Test"
    g.me = MagicMock()
    g.me.top_role = FakeRole(0, top_position)
    g.me.guild_permissions = discord.Permissions(manage_roles=manage_roles)
    table = {r.id: r for r in roles}
    g.get_role = lambda rid: table.get(rid)
    notify = MagicMock(spec=discord.TextChannel)
    notify.id, notify.name = 4242, "purchases"
    notify.send = AsyncMock()
    g.get_channel = lambda cid: notify if cid == 4242 else None
    g.notify = notify
    return g


def grant_ok(member):
    async def add_roles(*roles, reason=None):
        member.roles.extend(roles)
    member.add_roles = AsyncMock(side_effect=add_roles)


def forbid(member):
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions"))


async def test_shop_purchases():
    from cogs import shop as sm
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            sm = sys.modules[type(bot.get_cog("Shop")).__module__]
            role = FakeRole(555, 1)
            guild = shop_guild(roles=[role, FakeRole(777, 99)])
            ping = FakeRole(888, 1)
            guild.get_role = (lambda table: lambda rid: table.get(rid))({555: role, 777: FakeRole(777, 99), 888: ping})
            guild.fetch_member = AsyncMock()
            bot.get_guild = lambda gid: guild
            await db.set_setting(GUILD, "shop_notify_channel", 4242)
            await db.set_setting(GUILD, "shop_notify_role", 888)
            custom = await db.add_shop_item(GUILD, "Gift", "", 100, "custom", None, 3, 0, False)
            rid = await db.add_shop_item(GUILD, "VIP", "", 250, "role", 555, -1, 0, True)
            await db.admin_set(GUILD, 10, 1000, 1, "t")

            def notice():
                call = guild.notify.send.await_args
                return call.kwargs

            def fields(kw):
                return {f.name: f.value for f in kw["embed"].fields}

            # ---- منتج عادي: خصم مرة واحدة + إشعار بالقيم الصحيحة ومنشن الرتبة فقط
            m10 = buyer(10)
            out = await sm.complete_purchase(bot, guild, m10, custom)
            assert out.ok and (out.before, out.after) == (1000, 900) and await db.balance(GUILD, 10) == 900
            kw = notice()
            f = fields(kw)
            assert kw["content"] == "<@&888>" and kw["allowed_mentions"].roles == [ping] and not kw["allowed_mentions"].users
            assert "1,000" in f["⬅️ الرصيد قبل"] and "100" in f["➖ المخصوم"] and "900" in f["➡️ الرصيد بعد"]
            assert "Gift" in f["📦 المنتج"] and f"`#{out.purchase_id}`" == f["🧾 رقم الطلب"] and "<t:" in f["🕒 الوقت"]
            assert "<@10>" in f["👤 العضو"] and "بانتظار" in f["🏷️ الحالة"]
            assert (await db.purchase_row(GUILD, out.purchase_id))["status"] == "pending"

            # ---- رتبة: تُمنح ثم delivered ثم إشعار «مُنحت»
            grant_ok(m10)
            guild.fetch_member = AsyncMock(return_value=m10)
            out = await sm.complete_purchase(bot, guild, m10, rid)
            assert out.ok and role in m10.roles and await db.balance(GUILD, 10) == 650
            assert (await db.purchase_row(GUILD, out.purchase_id))["status"] == "delivered"
            assert "مُنحت" in fields(notice())["🏷️ الحالة"]
            # يملكها مسبقًا: لا خصم
            out2 = await sm.complete_purchase(bot, guild, m10, rid)
            assert not out2.ok and out2.code == "has_role" and await db.balance(GUILD, 10) == 650

            # ---- فشل المنح: لا خصم، المخزون سليم، تتبع إداري، إشعار فشل، ثم إعادة محاولة ناجحة بدون دفع مكرر
            await db.admin_set(GUILD, 11, 500, 1, "t")
            m11 = buyer(11)
            forbid(m11)
            before_tx = len(await db.transactions(GUILD, 11))
            out = await sm.complete_purchase(bot, guild, m11, rid)
            assert not out.ok and out.code == "role_failed" and out.retryable and "لم يُخصم" in out.message
            assert await db.balance(GUILD, 11) == 500
            pr = await db.purchase_row(GUILD, out.purchase_id)
            assert pr["status"] == "failed"
            kinds = [r["kind"] for r in await db.transactions(GUILD, 11)]
            assert kinds[:2] == ["purchase_reversal", "purchase"] and len(kinds) == before_tx + 2
            att = await db.purchase_attempts(GUILD)
            assert att and att[0]["outcome"] == "role_failed" and att[0]["user_id"] == 11
            kw = notice()
            assert "فشل" in kw["embed"].title and "<@&888>" == kw["content"]
            assert "0" in fields(kw)["➖ المخصوم"] and "500" in fields(kw)["➡️ الرصيد بعد"]
            # إعادة المحاولة عبر الزر
            view = sm.RetryView(bot, m11, guild, rid)
            inter = fake_interaction(m11)
            inter.user = m11
            guild.get_member = lambda uid: m11 if uid == 11 else None
            await view.retry.callback(inter)                       # ما زال يفشل: يبقى الزر
            assert inter.response.edit_message.await_args.kwargs["view"] is view and await db.balance(GUILD, 11) == 500
            grant_ok(m11)
            guild.fetch_member = AsyncMock(return_value=m11)
            inter2 = fake_interaction(m11)
            inter2.user = m11
            await view.retry.callback(inter2)                      # بعد إصلاح الصلاحيات: ينجح ويُخصم مرة واحدة
            assert inter2.response.edit_message.await_args.kwargs["view"] is None
            assert await db.balance(GUILD, 11) == 250 and role in m11.roles
            n_purchases = len([r for r in await db.fetchall("SELECT * FROM purchases WHERE user_id=11 AND item_id=?", (rid,))
                               if r["status"] == "delivered"])
            assert n_purchases == 1
            # سقف إعادة المحاولة
            await db.admin_set(GUILD, 12, 500, 1, "t")
            m12 = buyer(12)
            forbid(m12)
            v2 = sm.RetryView(bot, m12, guild, rid)
            guild.get_member = lambda uid: m12 if uid == 12 else None
            for _ in range(sm.MAX_RETRIES):
                i = fake_interaction(m12)
                i.user = m12
                await v2.retry.callback(i)
            assert i.response.edit_message.await_args.kwargs["view"] is None and await db.balance(GUILD, 12) == 500

            # ---- المنح «نجح» لكن الرتبة لم تظهر فعليًا عند العضو: يُعامل كفشل ويُعكس
            await db.admin_set(GUILD, 13, 500, 1, "t")
            m13 = buyer(13)
            m13.add_roles = AsyncMock()                            # طلب ناجح لكن بلا أثر
            guild.fetch_member = AsyncMock(return_value=buyer(13))
            out = await sm.complete_purchase(bot, guild, m13, rid)
            assert not out.ok and out.code == "role_failed" and await db.balance(GUILD, 13) == 500

            # ---- فحوص ما قبل الخصم: لا تغيير في الرصيد في كل الحالات
            await db.admin_set(GUILD, 14, 500, 1, "t")
            m14 = buyer(14)
            guild.me.guild_permissions = discord.Permissions(manage_roles=False)
            out = await sm.complete_purchase(bot, guild, m14, rid)
            assert out.code == "no_perm"
            guild.me.guild_permissions = discord.Permissions(manage_roles=True)
            high = await db.add_shop_item(GUILD, "Top", "", 10, "role", 777, -1, 0, True)
            out = await sm.complete_purchase(bot, guild, m14, high)
            assert out.code == "hierarchy"
            gone = await db.add_shop_item(GUILD, "Gone", "", 10, "role", 123456, -1, 0, True)
            assert (await sm.complete_purchase(bot, guild, m14, gone)).code == "no_role"
            await db.admin_set(GUILD, 15, 5, 1, "t")
            assert (await sm.complete_purchase(bot, guild, buyer(15), custom)).code == "insufficient"
            assert (await sm.complete_purchase(bot, guild, m14, 99999)).code == "not_found"
            await db.delete_shop_item(GUILD, custom)
            assert (await sm.complete_purchase(bot, guild, m14, custom)).code == "not_found"
            assert await db.balance(GUILD, 14) == 500 and await db.balance(GUILD, 15) == 5

            # ---- سباقات: نفس العضو يضغط عشر مرات = شراء واحد؛ ومخزون 2 لعشرة أعضاء = شراءان
            multi = await db.add_shop_item(GUILD, "Multi", "", 50, "custom", None, -1, 0, False)
            await db.admin_set(GUILD, 20, 1000, 1, "t")
            m20 = buyer(20)
            res = await asyncio.gather(*[sm.complete_purchase(bot, guild, m20, multi) for _ in range(10)])
            assert sum(1 for r in res if r.ok) == 1 and sum(1 for r in res if r.code == "busy") == 9
            assert await db.balance(GUILD, 20) == 950
            scarce = await db.add_shop_item(GUILD, "Scarce", "", 10, "custom", None, 2, 0, False)
            users = list(range(30, 40))
            for u in users:
                await db.admin_set(GUILD, u, 100, 1, "t")
            res = await asyncio.gather(*[sm.complete_purchase(bot, guild, buyer(u), scarce) for u in users])
            assert sum(1 for r in res if r.ok) == 2
            assert sum([100 - await db.balance(GUILD, u) for u in users]) == 20
            assert (await db.shop_item(GUILD, scarce))["stock"] == 0
            vip_once = await db.add_shop_item(GUILD, "Once", "", 20, "role", 555, -1, 0, True)
            few = [buyer(u) for u in range(50, 58)]
            for m in few:
                grant_ok(m)
                await db.admin_set(GUILD, m.id, 100, 1, "t")
            # نفس العضو الواحد عبر قاعدة البيانات مباشرة (يتجاوز حارس الواجهة): one_per_user يمنع التكرار
            res = await asyncio.gather(*[db.purchase(GUILD, 50, vip_once) for _ in range(6)], return_exceptions=True)
            assert sum(1 for r in res if not isinstance(r, Exception)) == 1 and await db.balance(GUILD, 50) == 80

            # ---- استعادة بعد انهيار بين الخصم والمنح
            import time as _t
            await db.admin_set(GUILD, 60, 400, 1, "t")
            m60 = buyer(60)
            grant_ok(m60)
            guild.fetch_member = AsyncMock(return_value=m60)
            guild.get_member = lambda uid: m60 if uid == 60 else None
            pid, _ = await db.purchase(GUILD, 60, rid)               # يخصم ويبقى «processing»
            assert (await db.purchase_row(GUILD, pid))["status"] == "processing" and await db.balance(GUILD, 60) == 150
            assert await sm.recover_processing(bot) == 0             # حديث جدًا: لا يُمس
            await db.execute("UPDATE purchases SET created_at=? WHERE id=?", (int(_t.time()) - 500, pid))
            assert await sm.recover_processing(bot) == 1
            assert (await db.purchase_row(GUILD, pid))["status"] == "delivered" and role in m60.roles
            assert await sm.recover_processing(bot) == 0             # لا تكرار
            await db.admin_set(GUILD, 61, 400, 1, "t")
            m61 = buyer(61)
            forbid(m61)
            guild.get_member = lambda uid: m61 if uid == 61 else None
            pid2, _ = await db.purchase(GUILD, 61, rid)
            await db.execute("UPDATE purchases SET created_at=? WHERE id=?", (int(_t.time()) - 500, pid2))
            assert await sm.recover_processing(bot) == 1
            assert (await db.purchase_row(GUILD, pid2))["status"] == "failed" and await db.balance(GUILD, 61) == 400
            await db.admin_set(GUILD, 62, 400, 1, "t")
            pid3, _ = await db.purchase(GUILD, 62, rid)               # العضو غادر السيرفر
            await db.execute("UPDATE purchases SET created_at=? WHERE id=?", (int(_t.time()) - 500, pid3))
            guild.get_member = lambda uid: None
            assert await sm.recover_processing(bot) == 1 and await db.balance(GUILD, 62) == 400
            # عكس واحد فقط مهما تكرر الاستدعاء
            assert not await db.settle_purchase(GUILD, pid3, ok=False, reason="x") and await db.balance(GUILD, 62) == 400

            # ---- أمر متابعة الإخفاقات للإدارة
            cog = bot.get_cog("Shop")
            i = fake_interaction(fake_member(1, admin=True))
            await cog.failures.callback(cog, i)
            desc = i.response.send_message.await_args.kwargs["embed"].description
            assert "role_failed" not in desc and "فشل منح الرتبة" in desc and "عُكس" in desc

            # ---- بدون قناة إشعار: يرجع لقناة سجل الاقتصاد، وبدونهما لا ينهار
            await db.reset_setting(GUILD, "shop_notify_channel")
            await db.reset_setting(GUILD, "shop_notify_role")
            guild.notify.send.reset_mock()
            await sm.notify_purchase(bot, guild, m10, await db.shop_item(GUILD, rid), 1, 10, 5, role)
            guild.notify.send.assert_not_awaited()                   # لا قناة محددة
            await db.set_setting(GUILD, "economy_log_channel", 4242)
            await sm.notify_purchase(bot, guild, m10, await db.shop_item(GUILD, rid), 1, 10, 5, role)
            kw = guild.notify.send.await_args.kwargs
            assert "content" not in kw and kw["allowed_mentions"].roles is False or not kw["allowed_mentions"].roles
        finally:
            await db.close()
    print("OK shop purchases: charge-then-grant with full reversal on failure, retry w/o double charge, verified grant, "
          "pre-checks, races, crash recovery, admin notice with exact balances and role-only ping")


async def test_role_settings_in_panel():
    from bot.settings_schema import SETTINGS_BY_KEY, format_value, parse_value
    from cogs import panel as pm
    st = SETTINGS_BY_KEY["shop_notify_role"]
    assert st.kind == "role" and format_value(st, 0) == "`غير محددة`" and format_value(st, 5) == "<@&5>"
    assert parse_value(st, "<@&123456>") == 123456 and parse_value(st, "123456") == 123456
    for bad in ("", "abc"):
        try:
            parse_value(st, bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    try:
        parse_value(st, "<@&9>", lambda rid: False)
        raise AssertionError
    except ValueError:
        pass
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            admin = fake_member(1, admin=True)
            view = pm.PanelView(bot, admin, MagicMock(id=GUILD))
            view.section = pm.SECTIONS_BY_KEY["channels"]
            sel = pm.SettingSelect([st], await db.get_settings(GUILD))
            view.clear_items()
            view.add_item(sel)
            sel._values = ["shop_notify_role"]
            await sel.callback(fake_interaction(admin))
            assert view.mode == "role_setting" and view.target == "shop_notify_role"
            await view.build(admin)
            assert any(isinstance(c, pm.RolePicker) for c in view.children) and len(view.children) <= 25

            async def choose(user, chosen):
                """يعيد بناء اللوحة (كما يحدث فعليًا) ثم يحاكي اختيار الرتبة من القائمة."""
                view.mode, view.target = "role_setting", "shop_notify_role"
                await view.build(admin)
                picker = next(c for c in view.children if isinstance(c, pm.RolePicker))
                picker._values = [chosen]
                i = fake_interaction(user)
                i.guild.get_role = lambda rid: chosen
                await picker.callback(i)

            r = FakeRole(321, 1)
            await choose(admin, FakeRole(1, 0, default=True))              # @everyone مرفوضة
            assert (await db.get_settings(GUILD))["shop_notify_role"] == 0
            await choose(admin, r)
            assert (await db.get_settings(GUILD))["shop_notify_role"] == 321
            await choose(fake_member(2), FakeRole(999, 1))                  # غير المسؤول لا يعدّل
            assert (await db.get_settings(GUILD))["shop_notify_role"] == 321
            view.mode, view.target = "role_setting", "shop_notify_role"     # الإلغاء
            await view.on_action(fake_interaction(admin), "clear_channel")
            assert (await db.get_settings(GUILD))["shop_notify_role"] == 0
        finally:
            await db.close()
    print("OK role-type settings: parse/format, panel picker, @everyone blocked, non-admin blocked, clear")


# ====================================================================== التحذيرات
async def test_warnings_db_and_migration():
    import sqlite3
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        path = os.path.join(d, "old.db")
        # قاعدة قديمة بجدول تحذيرات بالشكل السابق (بدون أعمدة الحالة) وفيها بيانات حقيقية
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, "
                  "user_id INTEGER NOT NULL, mod_id INTEGER NOT NULL, reason TEXT, created_at INTEGER NOT NULL)")
        c.execute("INSERT INTO warnings (guild_id,user_id,mod_id,reason,created_at) VALUES (1,5,9,'قديم',100)")
        c.commit()
        c.close()
        db = Database(path)
        await db.connect()
        try:
            old = await db.warnings(1, 5)
            assert len(old) == 1 and old[0]["status"] == "active" and old[0]["reason"] == "قديم"   # لم يضِع شيء
            assert await db.active_warning_count(1, 5) == 1
            # إضافة + منع التكرار (نفس العضو والمسؤول والسبب خلال النافذة)
            wid, count, dup = await db.add_warning_ex(1, 5, 9, "سبب جديد")
            assert (count, dup) == (2, False)
            wid2, count2, dup2 = await db.add_warning_ex(1, 5, 9, "سبب جديد")
            assert (wid2, count2, dup2) == (wid, 2, True)
            res = await asyncio.gather(*[db.add_warning_ex(1, 6, 9, "تزامن") for _ in range(12)])
            assert sum(1 for r in res if not r[2]) == 1 and await db.active_warning_count(1, 6) == 1
            # سبب مختلف أو مسؤول مختلف = تحذير جديد
            assert (await db.add_warning_ex(1, 6, 9, "سبب آخر"))[1] == 2
            assert (await db.add_warning_ex(1, 6, 10, "تزامن"))[1] == 3
            # بعد انتهاء نافذة التكرار يُسجَّل من جديد
            await db.execute("UPDATE warnings SET created_at=created_at-100 WHERE guild_id=1 AND user_id=6")
            assert (await db.add_warning_ex(1, 6, 9, "تزامن"))[2] is False
            # الإلغاء الناعم: يبقى السجل ولا يُحتسب، ولا يمكن إلغاؤه مرتين، ولا يلغي من سيرفر آخر
            row = await db.revoke_warning(1, wid, 77, "تمت المراجعة")
            assert row and row["reason"] == "سبب جديد" and await db.active_warning_count(1, 5) == 1
            assert await db.revoke_warning(1, wid, 77, "") is None and await db.revoke_warning(2, wid, 77, "") is None
            rec = await db.warning(1, wid)
            assert (rec["status"], rec["revoked_by"], rec["revoke_reason"]) == ("revoked", 77, "تمت المراجعة")
            assert len(await db.warnings(1, 5)) == 2 and len(await db.warnings(1, 5, include_revoked=False)) == 1
            # إلغاء الكل
            assert await db.revoke_all_warnings(1, 6, 77, "تنظيف") == 4 and await db.active_warning_count(1, 6) == 0
            assert await db.revoke_all_warnings(1, 6, 77, "") == 0
            assert len(await db.warnings(1, 6)) == 4                      # لا حذف: كلها ما زالت في السجل
            # استمرار البيانات بعد إعادة التشغيل (إغلاق وفتح)
            await db.close()
            db = Database(path)
            await db.connect()
            assert [w["status"] for w in await db.warnings(1, 5)] == ["revoked", "active"]
            assert await db.active_warning_count(1, 6) == 0 and len(await db.warnings(1, 6)) == 4
            assert await db.add_warning(1, 5, 9, "بعد الريستارت") == 2
        finally:
            await db.close()
    print("OK warnings DB: old-schema migration keeps data, dedupe (12 concurrent = 1), soft revoke, guild isolation, persistence")


async def test_warnings_commands():
    from bot import cards
    from bot.checks import NoPermission
    from cogs import moderation as modm
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            bot._connection.user = SimpleNamespace(id=999)
            bot.send_log = AsyncMock()
            cog = bot.get_cog("Moderation")
            cog._guard = AsyncMock(return_value=True)                 # فحوص التسلسل الهرمي لها اختبارها
            mm = sys.modules[type(cog).__module__]
            assert [mm.stage_label(n) for n in (1, 2, 3, 4, 10)] == ["الإنذار الأول", "الإنذار الثاني", "الإنذار الثالث",
                                                                      "الإنذار رقم 4", "الإنذار رقم 10"]
            admin = fake_member(1, admin=True)
            normal = fake_member(2)
            staff = fake_member(3, role_ids=(700,))
            await db.set_perm_roles(GUILD, "moderation", [700])

            # ---- الصلاحيات من الخادم لكل أمر: العضو العادي ممنوع، صاحب رتبة الإدارة مسموح
            for cmd in (cog.warn, cog.warnings, cog.delwarn, cog.clearwarns):
                for user, allowed_ in ((normal, False), (staff, True), (admin, True)):
                    i = fake_interaction(user)
                    i.client = bot
                    try:
                        ok = await cmd._check_can_run(i)
                    except NoPermission:
                        ok = False
                    assert ok == allowed_, (cmd.name, user.id)

            def target(uid):
                t = fake_member(uid)
                t.display_name = f"عضو{uid}"
                t.send = AsyncMock()
                t.timeout = AsyncMock()
                t.kick = AsyncMock()
                t.display_avatar = SimpleNamespace(url="https://x/a.png", with_size=lambda n: SimpleNamespace(
                    read=AsyncMock(return_value=b"junk")))
                return t

            t = target(50)
            guild = MagicMock()
            guild.id, guild.name = GUILD, "Test"
            guild.me = SimpleNamespace(id=999, mention="<@999>")
            guild.get_member = lambda uid: t if uid == 50 else None

            # ---- /warn: تسجيل، مرحلة الإنذار من العدد الفعلي، DM للعضو بلا كشف المسؤول، سجل إداري
            def winter(user=staff):
                i = fake_interaction(user)
                i.guild = guild
                i.user = user
                i.original_response = AsyncMock(return_value=MagicMock())
                return i

            i = winter()
            await cog.warn.callback(cog, i, member=t, reason="سبب أول")
            emb = i.response.send_message.await_args.kwargs["embed"]
            assert "الإنذار الأول" in emb.title and {f.name: f.value for f in emb.fields}["التحذيرات النشطة"] == "**1**"
            dm = t.send.await_args.kwargs["embed"]
            assert "سبب أول" in dm.description and "<@3>" not in str(dm.to_dict()) and "الإنذار الأول" in dm.title
            assert bot.send_log.await_count >= 1
            i = winter()
            await cog.warn.callback(cog, i, member=t, reason="سبب ثان")
            assert "الإنذار الثاني" in i.response.send_message.await_args.kwargs["embed"].title
            # تفاعل مكرر لنفس التحذير: لا صف جديد ولا DM ولا سجل ولا عقوبة
            sends, logs = t.send.await_count, bot.send_log.await_count
            i = winter()
            await cog.warn.callback(cog, i, member=t, reason="سبب ثان")
            assert "للتو" in i.response.send_message.await_args.kwargs["embed"].title
            assert i.response.send_message.await_args.kwargs["ephemeral"] is True
            assert t.send.await_count == sends and bot.send_log.await_count == logs
            assert await db.active_warning_count(GUILD, 50) == 2
            # الثالث: العقوبة التلقائية القائمة (كتم عند 3) تبقى كما هي دون تغيير
            i = winter()
            await cog.warn.callback(cog, i, member=t, reason="سبب ثالث")
            assert "الإنذار الثالث" in i.response.send_message.await_args.kwargs["embed"].title
            t.timeout.assert_awaited_once()
            t.kick.assert_not_awaited()                              # لا طرد/حظر تلقائي دون إعداد صريح

            # ---- /warnings: بطاقة مرئية بصفحات (5 تحذيرات = صفحتان) وسرّية للمسؤول فقط
            for n in range(3):
                await db.add_warning_ex(GUILD, 50, 3, f"سبب إضافي طويل جدًا {n} " * 5)
            i = winter()
            i.followup = MagicMock()
            i.followup.send = AsyncMock()
            await cog.warnings.callback(cog, i, member=t)
            kw = i.followup.send.await_args.kwargs
            assert kw["ephemeral"] is True and kw["file"].filename == "warnings.png" and kw["view"] is not None
            # لا سجل: رسالة سرّية
            i = winter()
            await cog.warnings.callback(cog, i, member=target(51))
            assert i.response.send_message.await_args.kwargs["ephemeral"] is True
            # فشل الرسم: بديل نصي منظم بحالات النشط/الملغى
            real = cards.render_warnings
            cards.render_warnings = lambda **k: (_ for _ in ()).throw(RuntimeError("x"))
            try:
                i = winter()
                i.followup = MagicMock()
                i.followup.send = AsyncMock()
                await cog.warnings.callback(cog, i, member=t)
                txt = (i.followup.send.await_args or i.response.send_message.await_args).kwargs["embed"]
                assert "نشط" in txt.description and "سبب أول" in txt.description
            finally:
                cards.render_warnings = real

            # ---- /delwarn: إلغاء ناعم + تسجيل إداري + لا يلغي مرتين
            first = (await db.warnings(GUILD, 50))[-1]["id"]
            bot.send_log.reset_mock()
            i = winter()
            await cog.delwarn.callback(cog, i, warn_id=first, reason="خطأ إداري")
            assert "تم إلغاء" in i.response.send_message.await_args.kwargs["embed"].title
            logged = bot.send_log.await_args.args[2]
            assert "إلغاء تحذير" in logged.title and "خطأ إداري" in str(logged.to_dict()) and "سبب أول" in str(logged.to_dict())
            assert await db.active_warning_count(GUILD, 50) == 5 and (await db.warning(GUILD, first))["status"] == "revoked"
            i = winter()
            await cog.delwarn.callback(cog, i, warn_id=first, reason=None)
            assert "غير موجود" in i.response.send_message.await_args.kwargs["embed"].description
            i = winter()
            await cog.delwarn.callback(cog, i, warn_id=99999, reason=None)
            assert i.response.send_message.await_args.kwargs["ephemeral"] is True
            # ---- /clearwarns: يلغي الكل ويسجل ولا يحذف
            bot.send_log.reset_mock()
            i = winter()
            await cog.clearwarns.callback(cog, i, member=t, reason="بداية جديدة")
            assert await db.active_warning_count(GUILD, 50) == 0 and len(await db.warnings(GUILD, 50)) == 6
            assert "إلغاء كل التحذيرات" in bot.send_log.await_args.args[2].title

            # ---- /userinfo يعدّ النشطة فقط
            assert await db.active_warning_count(GUILD, 50) == 0
        finally:
            await db.close()
    print("OK warnings commands: server-side permissions per command, stage from real count, DM w/o admin identity, "
          "dup-click ignored, existing auto-timeout kept (no auto kick/ban), card + paging + text fallback, "
          "revoke logged and non-destructive")


def test_warnings_card():
    from PIL import Image
    from bot import cards
    import io as _io
    items = [dict(id=i, date="2026-10-17", mod=n, reason=r, status=st) for i, (n, r, st) in enumerate([
        ("محمد", "x" * 300, "active"), ("A" * 60, "", "revoked"), ("🔥", "سبب قصير", "active"), ("النظام (تلقائي)", "ب", "revoked")], 1)]
    for count in (0, 1, 2, 3, 9):
        for its in (items, items[:1], []):
            png = cards.render_warnings(name="محمد عبدالله الطويل جدًا", avatar=b"junk", active=count, revoked=2,
                                        stage="الإنذار الثاني", items=its, page=1, pages=3, guild_name="g" * 80)
            assert Image.open(_io.BytesIO(png)).size == (1280, 720)
    print("OK warnings card: 1280x720 with long text, empty list, bad avatar, all severity levels")


# ====================================================================== الموسيقى: قائمة ولوحة تحكم
class FakeVC:
    """عميل صوت وهمي يحاكي سلوك discord.VoiceClient (تشغيل/إيقاف/استئناف/إيقاف نهائي + عدّاد الإطارات)."""

    def __init__(self, guild, channel):
        self.guild, self.channel = guild, channel
        self._connected, self._playing, self._paused = True, False, False
        self.source = self.after = None
        self._player = SimpleNamespace(loops=0)
        self.played, self.disconnected = [], False
        self.play_error = None

    def is_connected(self):
        return self._connected

    def is_playing(self):
        return self._playing and not self._paused

    def is_paused(self):
        return self._paused

    def play(self, source, after=None):
        if self.play_error:
            raise self.play_error
        if self._playing:
            raise discord.ClientException("Already playing audio.")
        self.source, self._playing, self._paused, self.after = source, True, False, after
        self._player = SimpleNamespace(loops=0)
        self.played.append(source)

    def stop(self):
        if self._playing:
            self._playing = self._paused = False
            cb, self.after = self.after, None
            if cb:
                cb(None)

    def pause(self):
        if self._playing:
            self._paused = True

    def resume(self):
        self._paused = False

    async def disconnect(self, force=False):
        self._connected, self.disconnected = False, True
        self.guild.voice_client = None

    def finish(self, error=None):
        """انتهاء الأغنية طبيعيًا (أو بخطأ من ffmpeg)."""
        self._playing = self._paused = False
        cb, self.after = self.after, None
        if cb:
            cb(error)


async def until(cond, timeout=6.0):
    t = asyncio.get_running_loop().time()
    while not cond():
        if asyncio.get_running_loop().time() - t > timeout:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


def music_world(bot, gid=GUILD):
    """سيرفر + روم صوتي + روم نصي + عميل صوت وهمي."""
    guild = MagicMock()
    guild.id, guild.name = gid, f"G{gid}"
    guild.me = MagicMock()
    chan = MagicMock(spec=discord.VoiceChannel)
    chan.id, chan.mention = 7000 + gid % 1000, "<#7>"
    chan.members = [SimpleNamespace(bot=True, id=999)]
    chan.permissions_for = lambda _m: discord.Permissions.all()
    chan.connect = AsyncMock()
    guild.voice_client = FakeVC(guild, chan)
    guild.get_channel = lambda cid: None
    text = MagicMock()
    panel_msg = MagicMock()
    panel_msg.edit = AsyncMock()
    text.send = AsyncMock(return_value=panel_msg)
    return guild, chan, text, panel_msg


def listener(uid, chan, *, roles=(), admin=False):
    m = fake_member(uid, admin=admin, role_ids=roles)
    m.voice = SimpleNamespace(channel=chan)
    m.display_name = f"u{uid}"
    chan.members.append(m)
    return m


def mk_track(user, n, dur=200):
    mm_ = sys.modules["cogs.music"]
    return mm_.Track(f"Song{n}", f"https://y/{n}", f"s{n}", dur, None, user, __import__("time").monotonic())


async def test_music_system():
    import time as _time
    from bot.checks import NoPermission
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            cog = bot.get_cog("Music")
            mm = sys.modules[type(cog).__module__]
            bot._connection.user = SimpleNamespace(id=999)

            class DummySource(discord.AudioSource):
                def __init__(self, stream, **_):
                    self.stream = stream

                def read(self):
                    return b""

                def is_opus(self):
                    return False

                def cleanup(self):
                    pass

            real_ff, real_fetch_img = mm.discord.FFmpegPCMAudio, mm.images.fetch
            mm.discord.FFmpegPCMAudio = DummySource
            mm.images.fetch = AsyncMock(return_value=None)
            try:
                guild, chan, text, panel_msg = music_world(bot)
                owner = listener(40, chan)
                other = listener(41, chan)
                staff = listener(42, chan, roles=(700,))
                await db.set_perm_roles(GUILD, "music", [700])
                outsider = fake_member(43)
                outsider.voice = None
                player = cog.get_player(guild)
                player.text_channel = text
                vc = guild.voice_client
                tracks = [mk_track(owner, i) for i in (1, 2, 3)]

                # ---- تتابع تلقائي + لوحة واحدة تُحدَّث بالتعديل لا برسائل جديدة
                for t in tracks:
                    assert player.enqueue(t) is not None
                await until(lambda: player.current is tracks[0] and vc.played)
                await until(lambda: text.send.await_count == 1)
                sent = text.send.await_args.kwargs
                assert isinstance(sent["view"], mm.MusicPanelView) and sent["file"].filename == "nowplaying.png"
                vc.finish()
                await until(lambda: player.current is tracks[1])
                vc.finish()
                await until(lambda: player.current is tracks[2])
                assert text.send.await_count == 1 and panel_msg.edit.await_count >= 2   # تحديث في المكان
                assert [t.title for t in player.history] == ["Song1", "Song2"]
                # ---- السابق: تعود 2 ثم تكمل 3 من جديد، ولا تتكرر في التاريخ
                assert player.previous()
                await until(lambda: player.current is tracks[1] and len(vc.played) == 4)
                vc.finish()
                await until(lambda: player.current is tracks[2] and len(vc.played) == 5)
                assert [t.title for t in player.history] == ["Song1", "Song2"]
                # ---- إيقاف مؤقت/استئناف يعكسان الحالة الفعلية، ووقت التشغيل من إطارات الصوت
                vc._player.loops = 500
                assert abs(player.elapsed() - 10.0) < 1e-9
                assert player.toggle_pause() is True and vc.is_paused() and player.paused
                assert player.toggle_pause() is False and vc.is_playing() and not player.paused
                vc._player = None
                assert player.elapsed() is None                       # لا نعرض تقدمًا غير دقيق
                vc._player = SimpleNamespace(loops=0)
                # ---- الصوت: خطوات وحدود، ويطبَّق فورًا على المصدر الجاري
                assert player.set_volume(65) == 65 and abs(vc.source.volume - 0.65) < 1e-9
                assert player.set_volume(1) == mm.MIN_VOLUME and player.set_volume(999) == mm.MAX_VOLUME
                player.set_volume(50)

                # ---- صلاحيات الأزرار من الخادم
                view = player.panel_view
                assert isinstance(view, mm.MusicPanelView)

                async def press(user, action, gid=GUILD):
                    i = fake_interaction(user, gid)
                    i.user = user
                    await view.handle(i, action)
                    denied = (i.response.send_message.await_args is not None and
                              "غير مسموح" in str(i.response.send_message.await_args.kwargs.get("embed").to_dict())
                              if i.response.send_message.await_args else False)
                    return denied

                assert await press(outsider, "pause") and await press(owner, "pause", gid=999) is True      # خارج الروم/سيرفر آخر
                assert not await press(other, "pause") and vc.is_paused()                                # أي مستمع يوقف مؤقتًا
                assert not await press(other, "pause") and vc.is_playing()                               # ويستأنف
                for action in ("skip", "stop", "loop", "vol_up", "vol_down", "previous"):
                    assert await press(other, action), action                                            # مستمع عادي: ممنوع
                assert player.current is tracks[2] and not player.loop and abs(player.volume - 0.5) < 1e-9
                assert not await press(staff, "vol_up") and abs(player.volume - 0.6) < 1e-9              # صاحب صلاحية الموسيقى
                assert not await press(staff, "vol_down") and abs(player.volume - 0.5) < 1e-9
                assert not await press(owner, "loop") and player.loop                                   # صاحب الأغنية
                assert not await press(owner, "loop") and not player.loop
                assert not await press(owner, "refresh")
                # البوت وحده مع شخص واحد: يتحكم
                chan.members[:] = [SimpleNamespace(bot=True, id=999), other]
                assert not await press(other, "vol_up") and abs(player.volume - 0.6) < 1e-9
                chan.members[:] = [SimpleNamespace(bot=True, id=999), owner, other, staff]
                # تخطي بصاحب الأغنية: ينتقل للنهاية ثم القائمة فارغة
                assert not await press(owner, "skip")
                await until(lambda: player.current is None)
                assert [t.title for t in player.history][-1] == "Song3"

                # ---- فشل بدء تشغيل أغنية (مصدر معطوب): رسالة واضحة ثم الانتقال للتالية
                bad, good = mk_track(owner, 90), mk_track(owner, 91)
                bad.fetched_at = good.fetched_at = _time.monotonic()
                mm.discord.FFmpegPCMAudio = lambda stream, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg died")) \
                    if stream == "s90" else DummySource(stream)
                text.send.reset_mock()
                player.enqueue(bad)
                player.enqueue(good)
                await until(lambda: player.current is good)
                notes = [c.kwargs.get("embed") for c in text.send.await_args_list if c.kwargs.get("embed")]
                assert any("Song90" in (e.description or "") and "تعذّر" in (e.description or "") for e in notes)
                # خطأ أثناء التشغيل نفسه (انهيار ffmpeg): إشعار ثم متابعة
                text.send.reset_mock()
                nxt = mk_track(owner, 92)
                nxt.fetched_at = _time.monotonic()
                player.enqueue(nxt)
                vc.finish(error=RuntimeError("stream broke"))
                await until(lambda: player.current is nxt)
                assert any("تعطل التشغيل" in (c.kwargs["embed"].title or "") for c in text.send.await_args_list
                           if c.kwargs.get("embed"))
                mm.discord.FFmpegPCMAudio = DummySource
                # خطأ من vc.play نفسه
                vc.finish()
                await until(lambda: player.current is None)
                vc.play_error = discord.ClientException("Already playing audio.")
                bad2 = mk_track(owner, 93)
                bad2.fetched_at = _time.monotonic()
                player.enqueue(bad2)
                await until(lambda: player.current is None and not player.queue)
                vc.play_error = None

                # ---- انتهاء القائمة: تتحول اللوحة لحالة «انتهت» وتُرفع الأزرار، ويغادر البوت (لا اتصال دائم)
                mm.IDLE_TIMEOUT = 0.3
                player.enqueue(mk_track(owner, 94))
                await until(lambda: player.current is not None)
                player.current.fetched_at = _time.monotonic()
                vc.finish()
                await until(lambda: GUILD not in cog.players, timeout=8)
                assert vc.disconnected and panel_msg.edit.await_args.kwargs["view"] is None
                mm.IDLE_TIMEOUT = 180

                # ---- مع الاتصال الدائم: لا يغادر أبدًا
                guild2, chan2, text2, panel2 = music_world(bot, gid=GUILD + 1)
                home = MagicMock(spec=discord.VoiceChannel)
                home.id = 5151
                guild2.get_channel = lambda cid: home if cid == 5151 else None
                await db.set_setting(GUILD + 1, "voice_home_channel", 5151)
                p2 = cog.get_player(guild2)
                p2.text_channel = text2
                u2 = listener(60, chan2)
                p2.enqueue(mk_track(u2, 1))
                await until(lambda: p2.current is not None)
                await p2.destroy("x")
                assert not guild2.voice_client.disconnected

                # ---- عزل السيرفرات: لكل سيرفر مشغّل وقائمة مستقلان
                g3, c3, t3, _ = music_world(bot, gid=GUILD + 2)
                g4, c4, t4, _ = music_world(bot, gid=GUILD + 3)
                pa, pb = cog.get_player(g3), cog.get_player(g4)
                assert pa is not pb and cog.get_player(g3) is pa
                pa.enqueue(mk_track(owner, 1))
                assert len(pa.queue) + (1 if pa.current else 0) == 1 and not pb.queue and pb.current is None
                for pl in (pa, pb):
                    await pl.destroy()
            finally:
                mm.discord.FFmpegPCMAudio, mm.images.fetch = real_ff, real_fetch_img
                mm.IDLE_TIMEOUT = 180
                for pl in list(cog.players.values()):
                    pl.task.cancel()
                cog.players.clear()
        finally:
            await db.close()
    print("OK music player: sequential play, in-place panel updates, previous/history, pause/resume, real elapsed, "
          "volume limits, per-button server-side permissions, failure recovery, idle exit, 24/7 stays, per-guild isolation")


async def test_music_queue_and_commands():
    from bot.checks import NoPermission
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            cog = bot.get_cog("Music")
            mm = sys.modules[type(cog).__module__]
            bot._connection.user = SimpleNamespace(id=999)
            guild, chan, text, panel_msg = music_world(bot)
            owner = listener(40, chan)
            staff = listener(42, chan, roles=(700,))
            normal = listener(44, chan)
            await db.set_perm_roles(GUILD, "music", [700])
            player = cog.get_player(guild)
            player.task.cancel()                       # لا نريد التشغيل الفعلي هنا: نختبر القائمة فقط
            player.text_channel = text
            tr = [mk_track(owner, i) for i in range(1, 26)]
            for t in tr:
                assert player.enqueue(t)
            assert [t.title for t in player.queue][:3] == ["Song1", "Song2", "Song3"]            # ترتيب الطلبات
            # حد القائمة
            for i in range(100):
                player.enqueue(mk_track(owner, 100 + i))
            assert len(player.queue) == mm.MAX_QUEUE and player.enqueue(mk_track(owner, 999)) is None
            player.clear_queue()
            for t in tr:
                player.enqueue(t)
            player.current = mk_track(owner, 0)

            # ---- /queue و /music queue: صفحات حية (25 أغنية = 3 صفحات)
            def inter(user):
                i = fake_interaction(user)
                i.user = user
                i.guild = guild
                i.client = bot
                i.original_response = AsyncMock(return_value=MagicMock())
                return i

            i = inter(normal)
            await cog.queue_alias.callback(cog, i)
            kw = i.response.send_message.await_args.kwargs
            assert "25" in kw["embed"].title and "الآن" in kw["embed"].description and kw["view"] is not None
            view = kw["view"]
            assert view.pages == 3 and not any(isinstance(c, mm._RemoveSelect) for c in view.children)   # لا إدارة للعادي
            # التنقل
            nav = inter(normal)
            await view._make_nav(1)(nav)
            assert "صفحة 2 من 3" in nav.response.edit_message.await_args.kwargs["embed"].footer.text
            await view._make_nav(1)(nav)
            await view._make_nav(1)(nav)
            assert view.page == 2 and len(view._chunk()) == 5
            # غير صاحب القائمة لا يستطيع استخدامها
            assert not await view.interaction_check(fake_interaction(staff))
            # المخوّل: يرى قائمة الحذف والمسح
            i = inter(staff)
            await cog.queue_alias.callback(cog, i)
            mview = i.response.send_message.await_args.kwargs["view"]
            assert any(isinstance(c, mm._RemoveSelect) for c in mview.children) and len(mview.children) <= 25
            target = list(player.queue)[3]
            ri = inter(staff)
            await mview.remove(ri, target.uid)
            assert target not in player.queue and len(player.queue) == 24
            # محاولة حذف مزوَّرة من عضو عادي عبر نفس الواجهة: تُرفض من الخادم
            fake_view = mm.QueueView(cog, player, normal, can_manage=True)       # لو ظهر الزر لسبب ما
            ri = inter(normal)
            await fake_view.remove(ri, list(player.queue)[0].uid)
            assert len(player.queue) == 24 and "صلاحية الموسيقى" in str(ri.response.send_message.await_args.kwargs["embed"].description)
            ri = inter(normal)
            await fake_view._clear(ri)
            assert len(player.queue) == 24
            # مسح القائمة بضغطتين
            ci = inter(staff)
            await mview._clear(ci)
            assert len(player.queue) == 24 and mview.confirm_clear
            ci = inter(staff)
            await mview._clear(ci)
            assert len(player.queue) == 0

            # ---- /music remove و /music clear: صلاحية الموسيقى من الخادم
            for cmd in (cog.remove, cog.clear):
                for user, ok_ in ((normal, False), (staff, True)):
                    ii = inter(user)
                    try:
                        allowed_ = await cmd._check_can_run(ii)
                    except NoPermission:
                        allowed_ = False
                    assert allowed_ == ok_, (cmd.name, user.id)
            for t in tr[:5]:
                player.enqueue(t)
            ii = inter(staff)
            await cog.remove.callback(cog, ii, position=2)
            assert [t.title for t in player.queue] == ["Song1", "Song3", "Song4", "Song5"]
            ii = inter(staff)
            await cog.remove.callback(cog, ii, position=50)
            assert ii.response.send_message.await_args.kwargs["ephemeral"] is True and len(player.queue) == 4
            ii = inter(staff)
            await cog.clear.callback(cog, ii)
            assert len(player.queue) == 0
            ii = inter(staff)
            await cog.clear.callback(cog, ii)
            assert ii.response.send_message.await_args.kwargs["ephemeral"] is True
            # قائمة فارغة وبلا مشغّل
            cog.players.clear()
            ii = inter(normal)
            await cog.queue_alias.callback(cog, ii)
            assert ii.response.send_message.await_args.kwargs["ephemeral"] is True
            player.task.cancel()
        finally:
            for pl in list(bot.get_cog("Music").players.values()):
                pl.task.cancel()
            await db.close()
    print("OK music queue: ordering, 100-track cap, live pages + navigation, admin remove/clear (select + confirm), "
          "server-side permission on forged UI actions, /music remove & clear permission checks")


async def test_music_rapid_requests_and_errors():
    import random as _r
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = await temp_db(d)
        try:
            bot = await make_bot(db)
            cog = bot.get_cog("Music")
            mm = sys.modules[type(cog).__module__]
            bot._connection.user = SimpleNamespace(id=999)
            guild, chan, text, panel_msg = music_world(bot)
            guild.voice_client = None                       # غير متصل: سيتصل عند أول طلب
            user = listener(40, chan)
            connects = []

            async def connect(**kw):
                connects.append(1)
                await asyncio.sleep(0.05)                   # اتصال بطيء ليتداخل مع بقية الطلبات
                if guild.voice_client is None:
                    guild.voice_client = FakeVC(guild, chan)
                else:
                    raise discord.ClientException("Already connected to a voice channel.")

            chan.connect = connect

            def inter():
                i = fake_interaction(user)
                i.user = user
                i.guild = guild
                i.channel = text
                i.followup = MagicMock()
                i.followup.send = AsyncMock()
                return i

            real_fetch, real_ff = mm.fetch_track, mm.discord.FFmpegPCMAudio

            class Dummy(discord.AudioSource):
                def __init__(self, *a, **k): pass
                def read(self): return b""
                def is_opus(self): return False

            mm.discord.FFmpegPCMAudio = Dummy
            mm.images.fetch = AsyncMock(return_value=None)

            async def fake_fetch(q, requester):
                await asyncio.sleep(_r.random() * 0.1)
                return mm.Track(q, f"https://y/{q}", "s", 100, None, requester, time_now())

            def time_now():
                import time as t_
                return t_.monotonic()

            mm.fetch_track = fake_fetch
            try:
                # 25 طلبًا متزامنًا: كلها تدخل القائمة دون ضياع أو تكرار، واتصال صوتي واحد فقط
                await asyncio.gather(*[cog._play(inter(), f"q{i}") for i in range(25)])
                player = cog.players[GUILD]
                titles = [player.current.title] if player.current else []
                titles += [t.title for t in player.queue]
                assert sorted(titles) == sorted(f"q{i}" for i in range(25)), titles
                assert len(set(t.uid for t in player.queue)) == len(player.queue) and len(connects) == 1
                player.task.cancel()
                # فشل البحث: رسالة مفهومة ولا اتصال ولا إضافة
                mm.fetch_track = AsyncMock(side_effect=Exception("Video unavailable"))
                before = len(player.queue)
                i = inter()
                await cog._play(i, "x")
                assert "غير متاح" in i.followup.send.await_args.kwargs["embed"].description and len(player.queue) == before
                # فشل الاتصال الصوتي بمهلة: رسالة مفهومة بلا إضافة
                cog.players.clear()
                guild.voice_client = None
                mm.fetch_track = fake_fetch

                async def slow_connect(**kw):
                    raise asyncio.TimeoutError
                chan.connect = slow_connect
                i = inter()
                await cog._play(i, "y")
                assert "مهلة الاتصال" in i.followup.send.await_args.kwargs["embed"].description
                assert not cog.players.get(GUILD) or not cog.players[GUILD].queue
                # قائمة ممتلئة
                chan.connect = connect
                guild.voice_client = FakeVC(guild, chan)
                p = cog.get_player(guild)
                p.task.cancel()
                for n in range(mm.MAX_QUEUE):
                    p.enqueue(mm.Track(f"f{n}", "u", "s", 1, None, user, 0.0))
                i = inter()
                await cog._play(i, "overflow")
                assert "ممتلئة" in i.followup.send.await_args.kwargs["embed"].description
            finally:
                mm.fetch_track, mm.discord.FFmpegPCMAudio = real_fetch, real_ff
        finally:
            for pl in list(bot.get_cog("Music").players.values()):
                pl.task.cancel()
            await db.close()
    print("OK music requests: 25 concurrent /play keep every track once with a single voice connect, search/connect failures "
          "give clear messages, full queue refused")


def test_music_card():
    from PIL import Image
    from bot import cards
    import io as _io
    cases = [
        dict(title="x" * 200, requester="سارة", duration="3:45", thumbnail=b"junk", queue_len=4, volume=50, loop=True,
             guild_name="g", artist="فيروز", elapsed=95.5, total=225, paused=False, next_title="التالي " * 30),
        dict(title="بث مباشر", requester="🔥", duration="مباشر", thumbnail=None, queue_len=0, volume=150, loop=False,
             guild_name="g", artist="قناة", artist_is_channel=True, elapsed=None, total=0, paused=True),
        dict(title="", requester="", duration="—", thumbnail=None, queue_len=0, volume=5, loop=False, guild_name="g",
             idle=True),
        dict(title="a", requester="b", duration="0:01", thumbnail=None, queue_len=1, volume=50, loop=False, guild_name="g",
             elapsed=99999, total=1),   # تجاوز: لا ينهار الشريط
    ]
    for kw in cases:
        assert Image.open(_io.BytesIO(cards.render_nowplaying(**kw))).size == (1280, 720)
    print("OK music card: progress/paused/live/idle/overflow states at 1280x720")


GUESSING = False
test_game_logic()
test_wheel()
test_gamecards()
asyncio.run(test_visual_fallback())
asyncio.run(test_registry_and_storage())
asyncio.run(test_shortcut_parsing_and_execution())
asyncio.run(test_commands_panel())
asyncio.run(test_shop_store_and_panel())
asyncio.run(test_shop_purchases())
asyncio.run(test_role_settings_in_panel())
asyncio.run(test_warnings_db_and_migration())
asyncio.run(test_warnings_commands())
test_warnings_card()
asyncio.run(test_music_system())
asyncio.run(test_music_queue_and_commands())
asyncio.run(test_music_rapid_requests_and_errors())
test_music_card()
test_info_cards()
asyncio.run(test_info_commands_use_cards_with_fallback())
asyncio.run(test_fast_game_and_trigger())
asyncio.run(test_voice_home_and_music())
asyncio.run(test_duplicate_command_cleanup())
test_visual_sizes_16x9()
asyncio.run(test_other_game_flows())
asyncio.run(test_roulette_flow())
asyncio.run(test_permissions())
asyncio.run(test_reward_idempotency())
asyncio.run(test_panel_security_and_layout())
print("ALL FEATURE TESTS PASSED")

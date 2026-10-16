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
from bot.db import Database  # noqa: E402
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


GUESSING = False
test_game_logic()
test_wheel()
asyncio.run(test_other_game_flows())
asyncio.run(test_roulette_flow())
asyncio.run(test_permissions())
asyncio.run(test_reward_idempotency())
asyncio.run(test_panel_security_and_layout())
print("ALL FEATURE TESTS PASSED")

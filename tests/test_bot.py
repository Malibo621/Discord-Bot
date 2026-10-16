"""اختبارات بدون اتصال: تحميل كل الأنظمة، التحقق من الأوامر، ومنطق الاقتصاد."""
import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import discord
from discord import app_commands
import main
from bot.db import Database, InsufficientFunds, LimitReached
from bot.utils import normalize, parse_duration, has_invisible, obfuscate

async def test_commands():
    bot = main.ServerBot()
    for cog in main.COGS:
        await bot.load_extension(cog)
    bot.get_cog("VoiceHome").watchdog.cancel()
    cmds = bot.tree.get_commands()
    total = 0
    def check(c, depth=0):
        nonlocal total
        d = c.to_dict(bot.tree)
        assert len(c.description) <= 100, (c.qualified_name, len(c.description))
        if isinstance(c, app_commands.Group):
            assert len(c.commands) <= 25
            for sub in c.commands: check(sub, depth+1)
        else:
            total += 1
            for p in c.parameters:
                assert len(p.description) <= 100, (c.qualified_name, p.name)
                for ch in p.choices: assert len(ch.name) <= 100
    for c in cmds: check(c)
    assert len(cmds) <= 100
    print(f"OK commands: {len(cmds)} top-level, {total} total")
    print("   ", ", ".join(sorted(c.name for c in cmds)))
    await bot.close() if bot.db.conn else None

async def test_economy():
    with tempfile.TemporaryDirectory() as d:
        db = Database(os.path.join(d, "t.db")); await db.connect()
        g, a, b = 1, 10, 20
        assert await db.game_reward(g, a, 300, "x", cap=500) == 300
        assert await db.game_reward(g, a, 300, "x", cap=500) == 200   # الحد اليومي
        assert await db.game_reward(g, a, 300, "x", cap=500) == 0
        assert await db.balance(g, a) == 500
        rec, tax = await db.transfer(g, a, b, 100, 5, 1000)
        assert (rec, tax) == (95, 5) and await db.balance(g, a) == 400 and await db.balance(g, b) == 95
        try: await db.transfer(g, a, b, 1000, 5, 5000); raise AssertionError
        except InsufficientFunds: pass
        assert await db.balance(g, a) == 400  # لم يتغير بعد الفشل
        try: await db.transfer(g, a, b, 350, 0, 400); raise AssertionError
        except LimitReached: pass
        # تحويلات متزامنة لا تنتج رصيدًا سالبًا
        res = await asyncio.gather(*[db.transfer(g, a, b, 100, 0, 0) for _ in range(10)], return_exceptions=True)
        assert sum(1 for r in res if not isinstance(r, Exception)) == 4 and await db.balance(g, a) == 0
        # المتجر
        item = await db.add_shop_item(g, "VIP", "", 50, "custom", None, 1, 0, True)
        pid, newbal = await db.purchase(g, b, item)
        assert newbal == 495 - 50
        try: await db.purchase(g, b, item); raise AssertionError
        except LimitReached: pass  # نفد المخزون/مكرر
        assert (await db.refund(g, pid, 1))["price"] == 50 and await db.balance(g, b) == 495
        assert (await db.shop_item(g, item))["stock"] == 1
        # الإدارة
        assert await db.admin_adjust(g, b, -10_000, 1, "t") == 0
        assert await db.admin_set(g, b, 77, 1, "t") == 77
        lb = await db.leaderboard(g); assert lb[0]["user_id"] == a or lb[0]["balance"] >= lb[-1]["balance"]
        txs = await db.transactions(g, b); assert len(txs) > 5
        # الإعدادات تبقى بعد إعادة التشغيل
        await db.set_setting(g, "reward_quiz", 99); await db.set_setting(g, "antilink_enabled", False)
        await db.close()
        db2 = Database(os.path.join(d, "t.db")); await db2.connect()
        s = await db2.get_settings(g)
        assert s["reward_quiz"] == 99 and s["antilink_enabled"] is False and await db2.balance(g, b) == 77
        await db2.close()
    print("OK economy: daily cap, tax, limits, concurrency, shop, refund, persistence")

def test_utils():
    assert normalize("إجابةٌ") == normalize("اجابه")
    assert has_invisible(obfuscate("كلمة")) and normalize(obfuscate("كلمة")) == normalize("كلمة")
    assert parse_duration("10m").total_seconds() == 600
    assert parse_duration("2س").total_seconds() == 7200
    assert parse_duration("1d12h").total_seconds() == 129600
    assert parse_duration("abc") is None
    print("OK utils")

test_utils()
asyncio.run(test_economy())
asyncio.run(test_commands())

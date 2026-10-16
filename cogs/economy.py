"""نظام الاقتصاد: المحفظة، التحويل، الترتيب، سجل العمليات، وتحكّم الإدارة بالنقاط."""
from __future__ import annotations

import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import ui
from bot.checks import has_category, require
from bot.db import InsufficientFunds, LimitReached
from bot.utils import earning_block_reason, ts

TX_LABELS = {
    "game": ("🎮", "فوز في لعبة"),
    "transfer_in": ("📥", "تحويل وارد"),
    "transfer_out": ("📤", "تحويل صادر"),
    "purchase": ("🛒", "شراء"),
    "refund": ("↩️", "استرجاع"),
    "admin_add": ("➕", "إضافة إدارية"),
    "admin_remove": ("➖", "خصم إداري"),
    "admin_set": ("✏️", "تعديل إداري"),
}


def format_tx(row) -> str:
    icon, label = TX_LABELS.get(row["kind"], ("•", row["kind"]))
    sign = "+" if row["amount"] >= 0 else ""
    other = f" • <@{row['other_id']}>" if row["other_id"] else ""
    note = f" • {row['note']}" if row["note"] else ""
    return f"{icon} `{sign}{row['amount']:,}` **{label}**{other}{note} — {ts(row['created_at'])}"


POINT_ACTIONS = {"add": "إضافة نقاط", "remove": "خصم نقاط", "set": "تعديل الرصيد"}


async def apply_points_change(bot, guild: discord.Guild, admin: discord.Member, member: discord.abc.User,
                              action: str, amount: int, reason: Optional[str]) -> tuple[bool, discord.Embed]:
    """تعديل إداري على النقاط (مشترك بين /points ولوحة التحكم) مع التسجيل في سجل الاقتصاد."""
    db = bot.db
    s = await db.get_settings(guild.id)
    if member.bot:
        return False, ui.error("لا يمكن تعديل نقاط البوتات.")
    if action not in POINT_ACTIONS or amount < 0 or (action != "set" and amount == 0):
        return False, ui.error("المبلغ غير صالح.")
    reason = (reason or "").strip() or "بدون سبب"
    old = await db.balance(guild.id, member.id)
    if action == "add":
        new = await db.admin_adjust(guild.id, member.id, amount, admin.id, reason)
        title, desc = "تمت إضافة النقاط", f"أُضيف {ui.money(amount, s)} إلى {member.mention}"
    elif action == "remove":
        new = await db.admin_adjust(guild.id, member.id, -amount, admin.id, reason)
        title, desc = "تم خصم النقاط", f"خُصم {ui.money(old - new, s)} من {member.mention}"
    else:
        new = await db.admin_set(guild.id, member.id, amount, admin.id, reason)
        title, desc = "تم تعديل الرصيد", f"رصيد {member.mention} أصبح {ui.money(amount, s)}"
    e = ui.success(title, f"{desc}\n\n" + ui.kv([("قبل", f"`{old:,}`"), ("بعد", ui.money(new, s)),
                                                 ("التغيير", ui.delta(new - old, s)), ("السبب", reason)]))
    log = ui.info("🛠️ تعديل إداري على النقاط",
                  f"**المسؤول:** {admin.mention}\n**العضو:** {member.mention}\n"
                  f"**الإجراء:** {title} (`{amount:,}`)\n**الرصيد:** `{old:,}` ← `{new:,}`\n**السبب:** {reason}",
                  ui.Colors.MOD)
    await bot.send_log(guild, "economy", log)
    return True, e


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_transfer: dict[tuple[int, int], float] = {}

    # ------------------------------------------------------------------ المحفظة
    @app_commands.command(name="balance", description="عرض محفظتك أو محفظة عضو آخر")
    @app_commands.describe(member="العضو (اختياري)")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        if member.bot:
            return await ui.reply(interaction, ui.error("البوتات لا تملك محافظ."), ephemeral=True)
        s = await self.bot.db.get_settings(interaction.guild_id)
        w = await self.bot.db.wallet(interaction.guild_id, member.id)
        e = ui.info(f"{s['currency_emoji']} محفظة {member.display_name}", color=ui.Colors.GOLD)
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="💰 الرصيد", value=ui.money(w["balance"], s), inline=False)
        e.add_field(name="🏅 الترتيب", value=f"`#{w['rank']}`")
        e.add_field(name="🎮 ألعاب فاز بها", value=f"`{w['games_won']:,}`")
        e.add_field(name="📈 إجمالي المكتسب", value=f"`{w['total_earned']:,}`")
        cap = s["daily_earn_cap"]
        if cap:
            e.add_field(name="📊 كسب اليوم من الألعاب",
                        value=f"{ui.progress_bar(w['earned_today'], cap)} `{w['earned_today']:,}/{cap:,}`",
                        inline=False)
        await ui.reply(interaction, ui.brand(e, interaction.guild))

    # ------------------------------------------------------------------ التحويل
    @app_commands.command(name="transfer", description="تحويل نقاط لعضو آخر")
    @app_commands.describe(member="العضو المستلم", amount="المبلغ")
    @app_commands.guild_only()
    async def transfer(self, interaction: discord.Interaction, member: discord.Member,
                       amount: app_commands.Range[int, 1, 1_000_000_000]):
        db = self.bot.db
        s = await db.get_settings(interaction.guild_id)
        sender = interaction.user

        if member.id == sender.id:
            return await ui.reply(interaction, ui.error("لا يمكنك التحويل لنفسك."), ephemeral=True)
        if member.bot:
            return await ui.reply(interaction, ui.error("لا يمكن التحويل للبوتات."), ephemeral=True)
        if amount < s["transfer_min"]:
            return await ui.reply(interaction, ui.error(f"أقل مبلغ للتحويل هو {ui.money(s['transfer_min'], s)}."),
                                  ephemeral=True)
        # منع الحسابات الجديدة من التحويل/الاستلام (مكافحة الحسابات الوهمية)
        for who, label in ((sender, "حسابك"), (member, "حساب المستلم")):
            reason = earning_block_reason(who, s)
            if reason:
                return await ui.reply(interaction, ui.error(f"{label} غير مؤهل للتحويل بعد.\n{reason}"),
                                      ephemeral=True)
        key = (interaction.guild_id, sender.id)
        wait = s["transfer_cooldown"] - (time.monotonic() - self._last_transfer.get(key, 0))
        if wait > 0:
            return await ui.reply(interaction, ui.error(f"انتظر **{wait:.0f}** ثانية قبل التحويل التالي."),
                                  ephemeral=True)
        bal = await db.balance(interaction.guild_id, sender.id)
        if bal < amount:
            return await ui.reply(interaction, ui.error(f"رصيدك غير كافٍ. رصيدك الحالي {ui.money(bal, s)}."),
                                  ephemeral=True)

        tax = amount * s["transfer_tax"] // 100
        e = ui.info("📤 تأكيد التحويل", color=ui.Colors.GOLD)
        e.add_field(name="المستلم", value=member.mention)
        e.add_field(name="المبلغ", value=ui.money(amount, s))
        if tax:
            e.add_field(name=f"الضريبة ({s['transfer_tax']}%)", value=ui.money(tax, s))
            e.add_field(name="سيصل للمستلم", value=ui.money(amount - tax, s), inline=False)
        view = ui.ConfirmView(sender.id)
        await ui.reply(interaction, e, view=view, ephemeral=True)
        await view.wait()
        if not view.value:
            msg = ui.info("تم إلغاء التحويل", color=ui.Colors.LOG)
            if view.interaction:
                return await view.interaction.response.edit_message(embed=msg, view=None)
            return await interaction.edit_original_response(embed=msg, view=None)

        try:
            received, tax = await db.transfer(interaction.guild_id, sender.id, member.id, amount,
                                              s["transfer_tax"], s["transfer_daily_limit"])
        except InsufficientFunds:
            return await view.interaction.response.edit_message(embed=ui.error("رصيدك لم يعد كافيًا."), view=None)
        except LimitReached as exc:
            return await view.interaction.response.edit_message(embed=ui.error(exc.message), view=None)

        self._last_transfer[key] = time.monotonic()
        await view.interaction.response.edit_message(embed=ui.success("تم التحويل"), view=None)
        done = ui.success("تم التحويل بنجاح",
                          f"{sender.mention} حوّل {ui.money(received, s)} إلى {member.mention}"
                          + (f"\nالضريبة: `{tax:,}`" if tax else ""))
        await interaction.followup.send(embed=ui.brand(done, interaction.guild))
        log = ui.info("📤 تحويل نقاط",
                      f"**من:** {sender.mention} (`{sender.id}`)\n**إلى:** {member.mention} (`{member.id}`)\n"
                      f"**المبلغ:** `{amount:,}` • **الضريبة:** `{tax:,}` • **المستلم:** `{received:,}`",
                      ui.Colors.LOG)
        await self.bot.send_log(interaction.guild, "economy", log)

    # ------------------------------------------------------------------ الترتيب
    @app_commands.command(name="leaderboard", description="ترتيب الأعضاء الأكثر نقاطًا")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        db = self.bot.db
        s = await db.get_settings(interaction.guild_id)
        total = await db.leaderboard_count(interaction.guild_id)
        if not total:
            return await ui.reply(interaction, ui.info(f"{ui.Icons.TROPHY} الترتيب",
                                                       "لا يوجد أحد في الترتيب بعد. العب وابدأ الجمع!"))
        per_page = 10
        rows = await db.leaderboard(interaction.guild_id, limit=min(total, 100))
        pages = []
        for start in range(0, len(rows), per_page):
            lines = []
            for i, r in enumerate(rows[start:start + per_page], start=start + 1):
                me = " ← **أنت**" if r["user_id"] == interaction.user.id else ""
                lines.append(f"{ui.rank_icon(i)} <@{r['user_id']}> — **{r['balance']:,}** {s['currency_emoji']}{me}")
            e = ui.info(f"{ui.Icons.TROPHY} ترتيب {s['currency_name']} في {interaction.guild.name}",
                        "\n".join(lines), ui.Colors.GOLD)
            if interaction.guild.icon:
                e.set_thumbnail(url=interaction.guild.icon.url)
            pages.append(e)
        w = await db.wallet(interaction.guild_id, interaction.user.id)
        for p in pages:
            p.add_field(name="ترتيبك", value=f"`#{w['rank']}` • {ui.money(w['balance'], s)}")
        await ui.Paginator(pages, interaction.user.id).start(interaction)

    # ------------------------------------------------------------------ سجل العمليات
    @app_commands.command(name="transactions", description="سجل عملياتك (أو سجل عضو/السيرفر للإدارة)")
    @app_commands.describe(member="عضو محدد (للإدارة)", server_wide="عرض سجل السيرفر كاملًا (للإدارة)")
    @app_commands.guild_only()
    async def transactions(self, interaction: discord.Interaction, member: Optional[discord.Member] = None,
                           server_wide: bool = False):
        db = self.bot.db
        target = member or interaction.user
        is_admin = await has_category(db, interaction.user, "economy")
        if (target.id != interaction.user.id or server_wide) and not is_admin:
            return await ui.reply(interaction, ui.error("يمكنك عرض سجلك فقط."), ephemeral=True)
        rows = await db.transactions(interaction.guild_id, None if server_wide else target.id, limit=50)
        title = "📜 سجل عمليات السيرفر" if server_wide else f"📜 سجل عمليات {target.display_name}"
        if not rows:
            return await ui.reply(interaction, ui.info(title, "لا توجد عمليات بعد."), ephemeral=True)
        pages = []
        for start in range(0, len(rows), 10):
            chunk = rows[start:start + 10]
            if server_wide:
                lines = [f"<@{r['user_id']}> {format_tx(r)}" for r in chunk]
            else:
                lines = [format_tx(r) for r in chunk]
            pages.append(ui.info(title, "\n".join(lines), ui.Colors.INFO))
        await ui.Paginator(pages, interaction.user.id).start(interaction, ephemeral=True)

    # ------------------------------------------------------------------ تحكم الإدارة
    points = app_commands.Group(name="points", description="التحكم بنقاط الأعضاء (للإدارة)", guild_only=True)

    async def _admin_change(self, interaction, member: discord.Member, action: str, amount: int, reason: str):
        ok, e = await apply_points_change(self.bot, interaction.guild, interaction.user, member, action, amount, reason)
        await ui.reply(interaction, ui.brand(e, interaction.guild) if ok else e, ephemeral=not ok)

    @points.command(name="add", description="إضافة نقاط لعضو")
    @app_commands.describe(member="العضو", amount="المبلغ", reason="السبب")
    @require("economy")
    async def points_add(self, interaction: discord.Interaction, member: discord.Member,
                         amount: app_commands.Range[int, 1, 100_000_000], reason: Optional[str] = None):
        await self._admin_change(interaction, member, "add", amount, reason)

    @points.command(name="remove", description="خصم نقاط من عضو")
    @app_commands.describe(member="العضو", amount="المبلغ", reason="السبب")
    @require("economy")
    async def points_remove(self, interaction: discord.Interaction, member: discord.Member,
                            amount: app_commands.Range[int, 1, 100_000_000], reason: Optional[str] = None):
        await self._admin_change(interaction, member, "remove", amount, reason)

    @points.command(name="set", description="تحديد رصيد عضو بقيمة معينة")
    @app_commands.describe(member="العضو", amount="الرصيد الجديد", reason="السبب")
    @require("economy")
    async def points_set(self, interaction: discord.Interaction, member: discord.Member,
                         amount: app_commands.Range[int, 0, 100_000_000], reason: Optional[str] = None):
        await self._admin_change(interaction, member, "set", amount, reason)

    @points.command(name="reset", description="تصفير رصيد عضو")
    @require("economy")
    async def points_reset(self, interaction: discord.Interaction, member: discord.Member,
                           reason: Optional[str] = None):
        await self._admin_change(interaction, member, "set", 0, reason or "تصفير")


async def setup(bot):
    await bot.add_cog(Economy(bot))

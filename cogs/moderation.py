"""أوامر الإدارة: تحذير، كتم، طرد، حظر، مسح الرسائل، قفل الرومات."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import ui
from bot.checks import require
from bot.utils import can_act_on, human_duration, parse_duration, ts

MAX_TIMEOUT = timedelta(days=28)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _log(self, guild: discord.Guild, action: str, target: discord.abc.User, mod: discord.abc.User,
                   reason: str, extra: str = "", color: int = ui.Colors.MOD):
        e = ui.info(f"{ui.Icons.HAMMER} {action}", color=color)
        e.add_field(name="العضو", value=f"{target.mention}\n`{target.id}`")
        e.add_field(name="المسؤول", value=mod.mention)
        e.add_field(name="السبب", value=reason or "بدون سبب", inline=False)
        if extra:
            e.add_field(name="تفاصيل", value=extra, inline=False)
        e.set_thumbnail(url=target.display_avatar.url)
        await self.bot.send_log(guild, "mod", e)

    async def _dm(self, member: discord.abc.User, guild: discord.Guild, action: str, reason: str, extra: str = ""):
        e = ui.warning(f"{action} في {guild.name}", f"**السبب:** {reason or 'بدون سبب'}" + (f"\n{extra}" if extra else ""))
        try:
            await member.send(embed=e)
        except discord.HTTPException:
            pass

    async def _guard(self, interaction: discord.Interaction, target: discord.Member) -> bool:
        problem = can_act_on(interaction.user, target)
        if problem:
            await ui.reply(interaction, ui.error(problem), ephemeral=True)
            return False
        return True

    # ------------------------------------------------------------------ التحذير
    @app_commands.command(name="warn", description="تحذير عضو")
    @app_commands.describe(member="العضو", reason="سبب التحذير")
    @app_commands.guild_only()
    @require("moderation")
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 400]):
        if not await self._guard(interaction, member):
            return
        await self.apply_warning(interaction.guild, member, interaction.user, reason)
        count = len(await self.bot.db.warnings(interaction.guild_id, member.id))
        await ui.reply(interaction, ui.brand(ui.warning(
            "تم تحذير العضو", f"{member.mention} — التحذير رقم **{count}**\n**السبب:** {reason}"), interaction.guild))

    async def apply_warning(self, guild: discord.Guild, member: discord.Member, mod: discord.abc.User,
                            reason: str) -> int:
        """يُستخدم أيضًا من نظام الحماية للتحذير التلقائي."""
        db = self.bot.db
        count = await db.add_warning(guild.id, member.id, mod.id, reason)
        s = await db.get_settings(guild.id)
        await self._dm(member, guild, "تلقيت تحذيرًا", reason, f"عدد تحذيراتك: **{count}**")
        await self._log(guild, f"تحذير (#{count})", member, mod, reason, color=ui.Colors.WARNING)

        if s["warn_kick_threshold"] and count >= s["warn_kick_threshold"]:
            try:
                await self._dm(member, guild, "تم طردك", f"وصلت إلى {count} تحذيرات")
                await member.kick(reason=f"طرد تلقائي: {count} تحذيرات")
                await self._log(guild, "طرد تلقائي", member, guild.me, f"وصل إلى {count} تحذيرات")
            except discord.HTTPException:
                pass
        elif s["warn_timeout_threshold"] and count >= s["warn_timeout_threshold"]:
            duration = timedelta(minutes=s["warn_timeout_minutes"])
            try:
                await member.timeout(min(duration, MAX_TIMEOUT), reason=f"كتم تلقائي: {count} تحذيرات")
                await self._log(guild, "كتم تلقائي", member, guild.me, f"وصل إلى {count} تحذيرات",
                                f"المدة: {human_duration(duration)}")
            except discord.HTTPException:
                pass
        return count

    @app_commands.command(name="warnings", description="عرض تحذيرات عضو")
    @app_commands.guild_only()
    @require("moderation")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        rows = await self.bot.db.warnings(interaction.guild_id, member.id)
        if not rows:
            return await ui.reply(interaction, ui.success("سجل نظيف", f"{member.mention} ليس لديه تحذيرات."),
                                  ephemeral=True)
        lines = [f"`#{w['id']}` {ts(w['created_at'], 'd')} • <@{w['mod_id']}>\n└ {w['reason']}" for w in rows[:20]]
        e = ui.info(f"⚠️ تحذيرات {member.display_name} ({len(rows)})", "\n".join(lines), ui.Colors.WARNING)
        e.set_thumbnail(url=member.display_avatar.url)
        await ui.reply(interaction, e, ephemeral=True)

    @app_commands.command(name="delwarn", description="حذف تحذير برقمه")
    @app_commands.guild_only()
    @require("moderation")
    async def delwarn(self, interaction: discord.Interaction, warn_id: int):
        ok = await self.bot.db.delete_warning(interaction.guild_id, warn_id)
        await ui.reply(interaction, ui.success("تم حذف التحذير") if ok else ui.error("رقم التحذير غير موجود."),
                       ephemeral=True)

    @app_commands.command(name="clearwarns", description="مسح كل تحذيرات عضو")
    @app_commands.guild_only()
    @require("moderation")
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        n = await self.bot.db.clear_warnings(interaction.guild_id, member.id)
        await ui.reply(interaction, ui.success("تم المسح", f"حُذف **{n}** تحذير من {member.mention}"))
        await self._log(interaction.guild, "مسح التحذيرات", member, interaction.user, f"{n} تحذير")

    # ------------------------------------------------------------------ الكتم
    @app_commands.command(name="mute", description="كتم عضو مؤقتًا (Timeout)")
    @app_commands.describe(member="العضو", duration="المدة: 10m / 2h / 1d / 1w أو 10د / 2س / 1ي",
                           reason="السبب")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @require("moderation")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, duration: str,
                   reason: Optional[str] = None):
        if not await self._guard(interaction, member):
            return
        td = parse_duration(duration)
        if not td:
            return await ui.reply(interaction, ui.error("مدة غير صحيحة. أمثلة: `10m` `2h` `1d` أو `30د` `2س`."),
                                  ephemeral=True)
        if td > MAX_TIMEOUT:
            return await ui.reply(interaction, ui.error("أقصى مدة للكتم 28 يومًا."), ephemeral=True)
        await member.timeout(td, reason=f"{interaction.user}: {reason or 'بدون سبب'}")
        await self._dm(member, interaction.guild, "تم كتمك", reason, f"المدة: **{human_duration(td)}**")
        await ui.reply(interaction, ui.brand(ui.success(
            "تم الكتم", f"{member.mention} مكتوم لمدة **{human_duration(td)}**\n**السبب:** {reason or 'بدون سبب'}"),
            interaction.guild))
        await self._log(interaction.guild, "كتم", member, interaction.user, reason,
                        f"المدة: {human_duration(td)} • ينتهي {ts(discord.utils.utcnow() + td)}")

    @app_commands.command(name="unmute", description="فك الكتم عن عضو")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @require("moderation")
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
        if not member.is_timed_out():
            return await ui.reply(interaction, ui.error("العضو غير مكتوم."), ephemeral=True)
        await member.timeout(None, reason=reason)
        await ui.reply(interaction, ui.success("تم فك الكتم", member.mention))
        await self._log(interaction.guild, "فك الكتم", member, interaction.user, reason, color=ui.Colors.SUCCESS)

    # ------------------------------------------------------------------ الطرد والحظر
    @app_commands.command(name="kick", description="طرد عضو من السيرفر")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @require("moderation")
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
        if not await self._guard(interaction, member):
            return
        await self._dm(member, interaction.guild, "تم طردك", reason)
        await member.kick(reason=f"{interaction.user}: {reason or 'بدون سبب'}")
        await ui.reply(interaction, ui.brand(ui.success("تم الطرد", f"{member.mention} (`{member}`)\n"
                                                                   f"**السبب:** {reason or 'بدون سبب'}"),
                                             interaction.guild))
        await self._log(interaction.guild, "طرد", member, interaction.user, reason, color=ui.Colors.ERROR)

    @app_commands.command(name="ban", description="حظر عضو")
    @app_commands.describe(user="العضو (أو آيدي لمن ليس في السيرفر)", delete_days="حذف رسائله لآخر كم يوم",
                           reason="السبب")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @require("moderation")
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  delete_days: app_commands.Range[int, 0, 7] = 0, reason: Optional[str] = None):
        member = interaction.guild.get_member(user.id)
        if member and not await self._guard(interaction, member):
            return
        if member:
            await self._dm(member, interaction.guild, "تم حظرك", reason)
        await interaction.guild.ban(user, reason=f"{interaction.user}: {reason or 'بدون سبب'}",
                                    delete_message_seconds=delete_days * 86400)
        await ui.reply(interaction, ui.brand(ui.success("تم الحظر", f"{user.mention} (`{user}`)\n"
                                                                   f"**السبب:** {reason or 'بدون سبب'}"),
                                             interaction.guild))
        await self._log(interaction.guild, "حظر", user, interaction.user, reason, color=ui.Colors.ERROR)

    @app_commands.command(name="unban", description="فك الحظر عن مستخدم بالآيدي")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @require("moderation")
    async def unban(self, interaction: discord.Interaction, user_id: str, reason: Optional[str] = None):
        if not user_id.isdigit():
            return await ui.reply(interaction, ui.error("أدخل آيدي صحيح."), ephemeral=True)
        try:
            user = await self.bot.fetch_user(int(user_id))
            await interaction.guild.unban(user, reason=reason)
        except discord.NotFound:
            return await ui.reply(interaction, ui.error("المستخدم غير محظور أو غير موجود."), ephemeral=True)
        await ui.reply(interaction, ui.success("تم فك الحظر", f"`{user}`"))
        await self._log(interaction.guild, "فك الحظر", user, interaction.user, reason, color=ui.Colors.SUCCESS)

    # ------------------------------------------------------------------ الرسائل والرومات
    @app_commands.command(name="clear", description="مسح عدد من الرسائل")
    @app_commands.describe(amount="العدد (1-100)", member="مسح رسائل عضو محدد فقط")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    @require("moderation")
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100],
                    member: Optional[discord.Member] = None):
        await interaction.response.defer(ephemeral=True)
        counter = {"n": 0}

        def check(m: discord.Message) -> bool:
            if member and m.author.id != member.id:
                return False
            if counter["n"] >= amount:
                return False
            counter["n"] += 1
            return True

        deleted = await interaction.channel.purge(limit=amount if not member else 300, check=check,
                                                  bulk=True, after=discord.utils.utcnow() - timedelta(days=14))
        await interaction.followup.send(embed=ui.success("تم المسح", f"حُذفت **{len(deleted)}** رسالة."),
                                        ephemeral=True)
        await self._log(interaction.guild, "مسح رسائل", member or interaction.user, interaction.user,
                        f"{len(deleted)} رسالة في {interaction.channel.mention}", color=ui.Colors.LOG)

    @app_commands.command(name="lock", description="قفل الروم أمام الأعضاء")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @require("moderation")
    async def lock(self, interaction: discord.Interaction, reason: Optional[str] = None):
        ow = interaction.channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow, reason=reason)
        await ui.reply(interaction, ui.warning("🔒 تم قفل الروم", reason))
        await self._log(interaction.guild, "قفل روم", interaction.user, interaction.user,
                        f"{interaction.channel.mention} • {reason or ''}")

    @app_commands.command(name="unlock", description="فتح الروم")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @require("moderation")
    async def unlock(self, interaction: discord.Interaction):
        ow = interaction.channel.overwrites_for(interaction.guild.default_role)
        ow.send_messages = None
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
        await ui.reply(interaction, ui.success("🔓 تم فتح الروم"))

    @app_commands.command(name="slowmode", description="تفعيل الوضع البطيء")
    @app_commands.describe(seconds="عدد الثواني (0 للإلغاء)")
    @app_commands.guild_only()
    @app_commands.checks.bot_has_permissions(manage_channels=True)
    @require("moderation")
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600]):
        await interaction.channel.edit(slowmode_delay=seconds)
        msg = f"⏳ الوضع البطيء: **{seconds}** ثانية" if seconds else "تم إلغاء الوضع البطيء"
        await ui.reply(interaction, ui.success(msg))


async def setup(bot):
    await bot.add_cog(Moderation(bot))

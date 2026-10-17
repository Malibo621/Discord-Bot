"""أوامر الإدارة: تحذير، كتم، طرد، حظر، مسح الرسائل، قفل الرومات."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import cards, images, ui
from bot.checks import require
from bot.utils import can_act_on, human_duration, parse_duration, ts

MAX_TIMEOUT = timedelta(days=28)
log = logging.getLogger("bot.moderation")
STAGES = {1: "الإنذار الأول", 2: "الإنذار الثاني", 3: "الإنذار الثالث"}


def stage_label(count: int) -> str:
    """مرحلة الإنذار من العدد الفعلي للتحذيرات النشطة (لا من نص ثابت)."""
    return STAGES.get(count, f"الإنذار رقم {count}")


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
    async def _display_user(self, guild: discord.Guild, user_id: int):
        user = guild.get_member(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        return user

    async def _log_ids(self, guild: discord.Guild, action: str, user_id: int, admin: discord.abc.User, reason: str,
                       extra: str = "", color: int = ui.Colors.MOD):
        user = await self._display_user(guild, user_id)
        e = ui.info(f"{ui.Icons.HAMMER} {action}", color=color)
        e.add_field(name="العضو", value=f"<@{user_id}>\n`{user_id}`")
        e.add_field(name="المسؤول", value=admin.mention)
        e.add_field(name="السبب", value=reason or "بدون سبب", inline=False)
        if extra:
            e.add_field(name="تفاصيل", value=extra, inline=False)
        if user is not None:
            e.set_thumbnail(url=user.display_avatar.url)
        await self.bot.send_log(guild, "mod", e)

    @app_commands.command(name="warn", description="تحذير عضو")
    @app_commands.describe(member="العضو", reason="سبب التحذير")
    @app_commands.guild_only()
    @require("moderation")
    async def warn(self, interaction: discord.Interaction, member: discord.Member,
                   reason: app_commands.Range[str, 1, 400]):
        if not await self._guard(interaction, member):
            return
        wid, count, dup = await self.apply_warning_ex(interaction.guild, member, interaction.user, reason)
        if dup:   # نفس التحذير بالضبط قبل لحظات (ضغط/تفاعل مكرر): لا تسجيل ولا عقوبة ولا إشعار ثان
            return await ui.reply(interaction, ui.warning(
                "سُجّل هذا التحذير للتو", f"لم يُضَف تحذير مكرر لـ {member.mention}.\n"
                                          f"التحذيرات النشطة: **{count}**"), ephemeral=True)
        e = ui.warning(f"تم تحذير العضو — {stage_label(count)}",
                       f"{member.mention}\n**السبب:** {reason}")
        e.add_field(name="رقم التحذير", value=f"`#{wid}`")
        e.add_field(name="التحذيرات النشطة", value=f"**{count}**")
        await ui.reply(interaction, ui.brand(e, interaction.guild))

    async def apply_warning(self, guild: discord.Guild, member: discord.Member, mod: discord.abc.User,
                            reason: str) -> int:
        """يُستخدم من نظام الحماية للتحذير التلقائي. يرجع عدد التحذيرات النشطة."""
        return (await self.apply_warning_ex(guild, member, mod, reason))[1]

    async def apply_warning_ex(self, guild: discord.Guild, member: discord.Member, mod: discord.abc.User,
                               reason: str) -> tuple[int, int, bool]:
        """يسجّل التحذير مرة واحدة ويطبّق إعدادات العقوبة التلقائية القائمة (كما هي، دون تغيير)."""
        db = self.bot.db
        wid, count, dup = await db.add_warning_ex(guild.id, member.id, mod.id, reason)
        if dup:
            return wid, count, True
        s = await db.get_settings(guild.id)
        await self._dm(member, guild, f"تلقيت تحذيرًا — {stage_label(count)}", reason,
                       f"عدد تحذيراتك النشطة: **{count}**")
        await self._log(guild, f"تحذير (#{wid}) — {stage_label(count)}", member, mod, reason, color=ui.Colors.WARNING)

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
        return wid, count, False

    @app_commands.command(name="warnings", description="عرض سجل تحذيرات عضو (بطاقة مرئية)")
    @app_commands.describe(member="العضو الذي تريد عرض تحذيراته")
    @app_commands.guild_only()
    @require("moderation")
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        db = self.bot.db
        rows = await db.warnings(interaction.guild_id, member.id)
        if not rows:
            return await ui.reply(interaction, ui.success("سجل نظيف", f"{member.mention} ليس لديه تحذيرات."),
                                  ephemeral=True)
        active = sum(1 for w in rows if w["status"] == "active")
        revoked = len(rows) - active
        s = await db.get_settings(interaction.guild_id)
        guild = interaction.guild

        def mod_name(mid: int) -> str:
            if mid == self.bot.user.id:
                return "النظام (تلقائي)"
            m = guild.get_member(mid)
            return m.display_name if m else f"مسؤول {mid}"

        if s["info_cards"] and cards.available():
            try:
                await interaction.response.defer(ephemeral=True)
                avatar = await images.avatar(member)
                pages = max(1, (len(rows) + 3) // 4)

                async def render(index: int):
                    chunk = rows[index * 4:(index + 1) * 4]
                    items = [dict(id=w["id"], date=datetime.fromtimestamp(w["created_at"], timezone.utc).strftime("%Y-%m-%d"),
                                  mod=mod_name(w["mod_id"]), reason=w["reason"], status=w["status"]) for w in chunk]
                    png = await images.branded(
                        guild, cards.render_warnings, name=member.display_name, avatar=avatar, active=active, revoked=revoked,
                        stage=stage_label(active) if active else "لا إنذارات نشطة", items=items, page=index + 1,
                        pages=pages, guild_name=guild.name)
                    return png, f"سجل تحذيرات {member.display_name}: {active} نشطة و{revoked} ملغاة"

                view = ui.ImagePaginator(interaction.user.id, pages, render, in_embed=s["game_image_in_embed"],
                                         name="warnings.png")
                return await view.start(interaction, ephemeral=True)
            except Exception:  # noqa: BLE001 - فشل الرسم لا يعطّل الأمر
                log.exception("Warnings card failed; using text fallback")
        lines = []
        for w in rows[:15]:
            state = "🟢 نشط" if w["status"] == "active" else "⚪ ملغى"
            lines.append(f"`#{w['id']}` {state} • {ts(w['created_at'], 'd')} • {mod_name(w['mod_id'])}\n└ {w['reason']}")
        e = ui.info(f"⚠️ تحذيرات {member.display_name}  ({active} نشطة · {revoked} ملغاة)", "\n".join(lines),
                    ui.Colors.WARNING)
        e.set_thumbnail(url=member.display_avatar.url)
        await ui.reply(interaction, e, ephemeral=True)

    @app_commands.command(name="delwarn", description="إلغاء تحذير برقمه (يبقى في السجل كملغى)")
    @app_commands.describe(warn_id="رقم التحذير (يظهر في /warnings)", reason="سبب الإلغاء (اختياري، يُسجَّل)")
    @app_commands.guild_only()
    @require("moderation")
    async def delwarn(self, interaction: discord.Interaction, warn_id: int, reason: Optional[str] = None):
        row = await self.bot.db.revoke_warning(interaction.guild_id, warn_id, interaction.user.id, reason or "")
        if not row:
            return await ui.reply(interaction, ui.error("رقم التحذير غير موجود أو ملغى مسبقًا."), ephemeral=True)
        await ui.reply(interaction, ui.success("تم إلغاء التحذير",
                                               f"`#{warn_id}` لـ <@{row['user_id']}> صار «ملغى» ولن يُحتسب."),
                       ephemeral=True)
        await self._log_ids(interaction.guild, f"إلغاء تحذير (#{warn_id})", row["user_id"], interaction.user,
                            reason or "بدون سبب", f"السبب الأصلي: {row['reason']}", ui.Colors.LOG)

    @app_commands.command(name="clearwarns", description="إلغاء كل التحذيرات النشطة لعضو (تبقى في السجل)")
    @app_commands.describe(member="العضو الذي ستُلغى كل تحذيراته النشطة", reason="سبب الإلغاء (اختياري، يُسجَّل)")
    @app_commands.guild_only()
    @require("moderation")
    async def clearwarns(self, interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = None):
        n = await self.bot.db.revoke_all_warnings(interaction.guild_id, member.id, interaction.user.id, reason or "")
        await ui.reply(interaction, ui.success("تم الإلغاء", f"أُلغي **{n}** تحذير نشط لـ {member.mention} "
                                                              "(تبقى مسجّلة كملغاة)."))
        await self._log(interaction.guild, "إلغاء كل التحذيرات", member, interaction.user,
                        reason or "بدون سبب", f"{n} تحذير", color=ui.Colors.LOG)

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

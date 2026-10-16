"""نظام الحماية: السبام، الروابط، المنشن الجماعي، التخريب (حذف/حظر جماعي)، والدخول الجماعي."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlparse

import discord
from discord.ext import commands

from bot import ui
from bot.checks import has_category

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
INVITE_RE = re.compile(r"(?:discord(?:app)?\.com/invite|discord\.gg|dsc\.gg|discord\.me)/[\w-]+", re.IGNORECASE)


class Protection(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.msg_times: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=60))
        self.last_content: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=5))
        self.link_strikes: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=10))
        self.nuke_actions: dict[tuple[int, int], deque] = defaultdict(lambda: deque(maxlen=50))
        self.joins: dict[int, deque] = defaultdict(lambda: deque(maxlen=200))
        self.raid_alerted: dict[int, float] = {}

    # ------------------------------------------------------------------ أدوات
    async def _exempt(self, member: discord.Member) -> bool:
        return member.bot or await has_category(self.bot.db, member, "bypass")

    async def _punish(self, message: discord.Message, title: str, reason: str, minutes: int = 0):
        member = message.author
        if minutes:
            try:
                await member.timeout(timedelta(minutes=minutes), reason=f"الحماية: {reason}")
            except discord.HTTPException:
                pass
        notice = ui.warning(title, f"{member.mention} {reason}"
                                   + (f"\nتم كتمك **{minutes}** دقيقة." if minutes else ""))
        try:
            await message.channel.send(embed=notice, delete_after=8)
        except discord.HTTPException:
            pass
        log = ui.info(f"{ui.Icons.SHIELD} {title}", color=ui.Colors.ERROR)
        log.add_field(name="العضو", value=f"{member.mention}\n`{member.id}`")
        log.add_field(name="الروم", value=message.channel.mention)
        log.add_field(name="الإجراء", value=reason + (f" • كتم {minutes} د" if minutes else ""), inline=False)
        if message.content:
            log.add_field(name="المحتوى", value=message.content[:1000], inline=False)
        await self.bot.send_log(message.guild, "mod", log)

    def _domain_allowed(self, url: str, allowed: list[str]) -> bool:
        if not url.lower().startswith("http"):
            url = "http://" + url
        host = (urlparse(url).hostname or "").lower()
        host = host[4:] if host.startswith("www.") else host
        return any(host == d or host.endswith("." + d) for d in allowed)

    # ------------------------------------------------------------------ الرسائل
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not isinstance(message.author, discord.Member):
            return
        if await self._exempt(message.author):
            return
        s = await self.bot.db.get_settings(message.guild.id)
        key = (message.guild.id, message.author.id)

        # 1) المنشن الجماعي
        mentions = len(set(message.raw_mentions)) + len(message.raw_role_mentions)
        if mentions >= s["mass_mention_limit"] or message.mention_everyone:
            if message.mention_everyone and message.author.guild_permissions.mention_everyone:
                pass
            else:
                await self._safe_delete(message)
                return await self._punish(message, "منشن جماعي", "تم حذف رسالتك بسبب المنشن الجماعي.",
                                          s["antispam_timeout_minutes"])

        # 2) الروابط والدعوات
        if s["antilink_enabled"] or s["antilink_block_invites"]:
            if s["antilink_block_invites"] and INVITE_RE.search(message.content):
                await self._safe_delete(message)
                return await self._link_strike(message, s, "دعوات السيرفرات الأخرى غير مسموحة.")
            if s["antilink_enabled"]:
                allowed = [d.strip().lower() for d in s["allowed_domains"].split(",") if d.strip()]
                for url in URL_RE.findall(message.content):
                    if not self._domain_allowed(url, allowed):
                        await self._safe_delete(message)
                        return await self._link_strike(message, s, "الروابط غير مسموحة هنا.")

        # 3) السبام (رسائل كثيرة أو مكررة) — لا يُطبق على روم فيه لعبة قائمة
        games = self.bot.get_cog("Games")
        in_game = games is not None and message.channel.id in games.active
        if s["antispam_enabled"] and not in_game:
            now = time.monotonic()
            times = self.msg_times[key]
            times.append(now)
            window = [t for t in times if now - t <= s["antispam_seconds"]]
            content = message.content.strip().lower()
            recent = self.last_content[key]
            recent.append((now, content))
            duplicates = sum(1 for t, c in recent if c and c == content and now - t <= 30)
            if len(window) >= s["antispam_messages"] or duplicates >= 4:
                times.clear()
                recent.clear()
                await self._purge_recent(message)
                await self._punish(message, "سبام", "توقف عن إرسال الرسائل بسرعة/بشكل متكرر.",
                                   s["antispam_timeout_minutes"])

    async def _safe_delete(self, message: discord.Message):
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    async def _purge_recent(self, message: discord.Message):
        try:
            await message.channel.purge(limit=30, check=lambda m: m.author.id == message.author.id,
                                        after=discord.utils.utcnow() - timedelta(seconds=30))
        except discord.HTTPException:
            pass

    async def _link_strike(self, message: discord.Message, s: dict, reason: str):
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        strikes = self.link_strikes[key]
        strikes.append(now)
        recent = [t for t in strikes if now - t <= 600]
        if len(recent) >= 3:
            strikes.clear()
            mod = self.bot.get_cog("Moderation")
            if mod:
                await mod.apply_warning(message.guild, message.author, message.guild.me,
                                        "تحذير تلقائي: تكرار نشر روابط غير مسموحة")
            return await self._punish(message, "روابط متكررة", reason + " (تحذير تلقائي)",
                                      s["antispam_timeout_minutes"])
        await self._punish(message, "رابط محظور", reason)

    # ------------------------------------------------------------------ الدخول الجماعي
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        s = await self.bot.db.get_settings(member.guild.id)
        now = time.monotonic()
        joins = self.joins[member.guild.id]
        joins.append(now)
        recent = [t for t in joins if now - t <= 10]
        if len(recent) >= s["raid_join_threshold"] and now - self.raid_alerted.get(member.guild.id, 0) > 120:
            self.raid_alerted[member.guild.id] = now
            e = ui.error(f"دخل **{len(recent)}** عضو خلال 10 ثوانٍ. قد يكون هجومًا.\n"
                         "يمكنك استخدام `/lock` لقفل الرومات ورفع مستوى التحقق من إعدادات السيرفر.",
                         title=f"{ui.Icons.SHIELD} تنبيه: دخول جماعي")
            await self.bot.send_log(member.guild, "mod", e)

    # ------------------------------------------------------------------ مكافحة التخريب
    async def _record_destructive(self, guild: discord.Guild, action: discord.AuditLogAction, label: str,
                                  target_id: int | None = None):
        s = await self.bot.db.get_settings(guild.id)
        if not s["antinuke_enabled"] or not guild.me.guild_permissions.view_audit_log:
            return
        try:
            entry = await anext(guild.audit_logs(limit=1, action=action), None)
        except discord.HTTPException:
            return
        if not entry or not entry.user or (discord.utils.utcnow() - entry.created_at).total_seconds() > 15:
            return
        if target_id and getattr(entry.target, "id", None) != target_id:
            return
        actor = guild.get_member(entry.user.id)
        if not actor or actor.id in (guild.owner_id, self.bot.user.id):
            return
        now = time.monotonic()
        key = (guild.id, actor.id)
        actions = self.nuke_actions[key]
        actions.append(now)
        recent = [t for t in actions if now - t <= 60]
        if len(recent) <= s["antinuke_threshold"]:
            return
        actions.clear()
        removed = []
        if actor.top_role < guild.me.top_role:
            roles = [r for r in actor.roles if not r.is_default() and not r.managed and r < guild.me.top_role]
            try:
                await actor.remove_roles(*roles, reason=f"الحماية من التخريب: {label} متكرر")
                removed = roles
            except discord.HTTPException:
                pass
        e = ui.error(f"{actor.mention} (`{actor.id}`) نفّذ **{len(recent)}** عمليات **{label}** خلال دقيقة.\n"
                     + (f"تم سحب رتبه: {' '.join(r.mention for r in removed)}" if removed
                        else "⚠️ تعذّر سحب رتبه (رتبته أعلى من البوت). تدخّل فورًا!"),
                     title=f"{ui.Icons.SHIELD} تم إيقاف محاولة تخريب")
        await self.bot.send_log(guild, "mod", e)
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(embed=e)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._record_destructive(channel.guild, discord.AuditLogAction.channel_delete, "حذف رومات")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._record_destructive(role.guild, discord.AuditLogAction.role_delete, "حذف رتب")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user):
        await self._record_destructive(guild, discord.AuditLogAction.ban, "حظر أعضاء", user.id)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        await self._record_destructive(member.guild, discord.AuditLogAction.kick, "طرد أعضاء", member.id)


async def setup(bot):
    await bot.add_cog(Protection(bot))

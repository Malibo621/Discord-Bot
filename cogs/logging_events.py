"""تسجيل الأحداث المهمة في السيرفر داخل روم السجل."""
from __future__ import annotations

import discord
from discord.ext import commands

from bot import ui
from bot.utils import ts


class EventLogger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _send(self, guild: discord.Guild, embed: discord.Embed):
        await self.bot.send_log(guild, "log", embed)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        e = ui.info("🗑️ حذف رسالة", color=ui.Colors.ERROR)
        e.add_field(name="الكاتب", value=f"{message.author.mention}\n`{message.author.id}`")
        e.add_field(name="الروم", value=message.channel.mention)
        if message.content:
            e.add_field(name="المحتوى", value=message.content[:1024], inline=False)
        if message.attachments:
            e.add_field(name="مرفقات", value="\n".join(a.filename for a in message.attachments)[:1024], inline=False)
        await self._send(message.guild, e)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: list[discord.Message]):
        if not messages or not messages[0].guild:
            return
        e = ui.info("🧹 حذف جماعي للرسائل", f"حُذفت **{len(messages)}** رسالة في {messages[0].channel.mention}",
                    ui.Colors.ERROR)
        await self._send(messages[0].guild, e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        e = ui.info("✏️ تعديل رسالة", f"[الانتقال للرسالة]({after.jump_url})", ui.Colors.WARNING)
        e.add_field(name="الكاتب", value=before.author.mention)
        e.add_field(name="الروم", value=before.channel.mention)
        e.add_field(name="قبل", value=(before.content or "—")[:1024], inline=False)
        e.add_field(name="بعد", value=(after.content or "—")[:1024], inline=False)
        await self._send(before.guild, e)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        e = ui.info("📥 عضو جديد", f"{member.mention} • `{member}`", ui.Colors.SUCCESS)
        e.add_field(name="عمر الحساب", value=ts(member.created_at))
        e.add_field(name="عدد الأعضاء", value=f"`{member.guild.member_count:,}`")
        e.set_thumbnail(url=member.display_avatar.url)
        await self._send(member.guild, e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        roles = " ".join(r.mention for r in member.roles if not r.is_default())
        e = ui.info("📤 غادر عضو", f"{member.mention} • `{member}`", ui.Colors.ERROR)
        if member.joined_at:
            e.add_field(name="انضم", value=ts(member.joined_at))
        if roles:
            e.add_field(name="الرتب", value=roles[:1024], inline=False)
        e.set_thumbnail(url=member.display_avatar.url)
        await self._send(member.guild, e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.nick != after.nick:
            e = ui.info("🏷️ تغيير اللقب", after.mention, ui.Colors.INFO)
            e.add_field(name="قبل", value=before.nick or "—")
            e.add_field(name="بعد", value=after.nick or "—")
            await self._send(after.guild, e)
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            e = ui.info("🎖️ تحديث الرتب", after.mention, ui.Colors.INFO)
            if added:
                e.add_field(name="➕ أُضيفت", value=" ".join(r.mention for r in added))
            if removed:
                e.add_field(name="➖ أُزيلت", value=" ".join(r.mention for r in removed))
            await self._send(after.guild, e)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        await self._send(guild, ui.info("🔓 فك حظر", f"{user.mention} • `{user}`", ui.Colors.SUCCESS))

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await self._send(channel.guild, ui.info("📁 إنشاء روم", f"{channel.mention} • `{channel.name}`",
                                                ui.Colors.SUCCESS))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._send(channel.guild, ui.info("🗂️ حذف روم", f"`#{channel.name}`", ui.Colors.ERROR))

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._send(role.guild, ui.info("🆕 إنشاء رتبة", role.mention, ui.Colors.SUCCESS))

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._send(role.guild, ui.info("🗑️ حذف رتبة", f"`{role.name}`", ui.Colors.ERROR))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot or before.channel == after.channel:
            return
        if not before.channel:
            desc = f"{member.mention} دخل {after.channel.mention}"
        elif not after.channel:
            desc = f"{member.mention} خرج من {before.channel.mention}"
        else:
            desc = f"{member.mention} انتقل من {before.channel.mention} إلى {after.channel.mention}"
        await self._send(member.guild, ui.info("🔊 الرومات الصوتية", desc, ui.Colors.LOG))


async def setup(bot):
    await bot.add_cog(EventLogger(bot))

"""نقطة تشغيل البوت."""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from bot import ui
from bot.checks import NoPermission
from bot.db import Database
from bot.settings_schema import PERMISSION_CATEGORIES

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s: %(message)s")
log = logging.getLogger("bot")

COGS = [
    "cogs.settings",
    "cogs.economy",
    "cogs.shop",
    "cogs.games",
    "cogs.music",
    "cogs.moderation",
    "cogs.protection",
    "cogs.logging_events",
    "cogs.info",
    "cogs.panel",
]


class ServerBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.voice_states = True
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.db = Database(os.getenv("DATABASE_PATH", "data/bot.db"))

    async def setup_hook(self) -> None:
        os.makedirs(os.path.dirname(self.db.path) or ".", exist_ok=True)
        await self.db.connect()
        for cog in COGS:
            await self.load_extension(cog)
        self.tree.on_error = self.on_app_command_error
        guild_id = os.getenv("GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d commands to guild %s", len(synced), guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global commands (may take up to an hour to appear)", len(synced))
        self.expire_roles.start()

    async def close(self) -> None:
        await self.db.close()
        await super().close()

    async def on_ready(self):
        log.info("Logged in as %s (%s) — %d servers", self.user, self.user.id, len(self.guilds))
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/help"))

    # ------------------------------------------------------------------ السجلات
    async def send_log(self, guild: discord.Guild, kind: str, embed: discord.Embed) -> None:
        """kind: log | mod | economy — يرسل للروم المحدد، ويرجع لروم السجل العام إن لم يُحدد."""
        settings = await self.db.get_settings(guild.id)
        channel_id = settings.get(f"{kind}_log_channel") if kind != "log" else settings["log_channel"]
        channel_id = channel_id or settings["log_channel"]
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.abc.Messageable):
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                pass

    # ------------------------------------------------------------------ انتهاء الرتب المؤقتة
    @tasks.loop(minutes=1)
    async def expire_roles(self):
        # أي خطأ غير معالج يوقف tasks.loop نهائيًا، لذلك يُعزل كل طلب على حدة
        for p in await self.db.expired_role_purchases():
            try:
                await self._expire_one(p)
            except Exception:  # noqa: BLE001
                log.exception("Failed to expire purchase #%s", p["id"])

    async def _expire_one(self, p) -> None:
        guild = self.get_guild(p["guild_id"])
        await self.db.set_purchase_status(p["id"], "expired")
        if not guild:
            return
        member = guild.get_member(p["user_id"])
        role = guild.get_role(p["role_id"]) if p["role_id"] else None
        if member and role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"انتهت مدة المنتج #{p['id']}")
            except discord.HTTPException:
                pass
        if member:
            e = ui.info("⏳ انتهت مدة منتج", f"انتهت مدة **{p['item_name']}** للعضو {member.mention}.", ui.Colors.LOG)
            await self.send_log(guild, "economy", e)

    @expire_roles.before_loop
    async def _before_expire(self):
        await self.wait_until_ready()

    # ------------------------------------------------------------------ الأخطاء
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, NoPermission):
            if error.category == "admin":
                msg = "هذا الأمر للمسؤولين أو أصحاب الصلاحيات المخصصة فقط."
            else:
                msg = f"لا تملك صلاحية **{PERMISSION_CATEGORIES[error.category][0]}**."
        elif isinstance(error, app_commands.CommandOnCooldown):
            msg = f"تمهّل قليلًا! يمكنك استخدام الأمر بعد **{error.retry_after:.0f}** ثانية."
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = "هذا الأمر يعمل داخل السيرفر فقط."
        elif isinstance(error, app_commands.BotMissingPermissions):
            msg = "البوت يحتاج صلاحيات إضافية: " + "، ".join(error.missing_permissions)
        elif isinstance(error, app_commands.CheckFailure):
            msg = "لا يمكنك استخدام هذا الأمر هنا."
        else:
            original = getattr(error, "original", error)
            if isinstance(original, discord.Forbidden):
                msg = "لا أملك الصلاحية الكافية لتنفيذ هذا الإجراء. تأكد من صلاحيات البوت وترتيب رتبته."
            else:
                log.exception("Unhandled command error", exc_info=original)
                msg = "حدث خطأ غير متوقع. تم تسجيله وسيتم مراجعته."
        try:
            await ui.reply(interaction, ui.error(msg), ephemeral=True)
        except discord.HTTPException:
            pass


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token or token.startswith("ضع"):
        sys.exit("ضع توكن البوت في ملف .env (DISCORD_TOKEN=...)")
    bot = ServerBot()
    try:
        async with bot:
            await bot.start(token)
    except discord.LoginFailure:
        sys.exit("\n[X] The bot token is wrong. Delete the .env file and run start again with a new token.\n")
    except discord.PrivilegedIntentsRequired:
        sys.exit("\n[X] Enable 'Server Members Intent' and 'Message Content Intent' in the Developer Portal "
                 "(Bot page), save, then run again.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

"""معلومات العضو والسيرفر، وقائمة المساعدة التفاعلية."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import cards, images, ui
from bot.utils import ts

log = logging.getLogger("bot.info")

# ترتيب وأوصاف أقسام قائمة المساعدة: (اسم الكوغ، العنوان، الأيقونة، الوصف)
HELP_SECTIONS = [
    ("Economy", "الاقتصاد والنقاط", "🪙", "المحفظة، التحويل، الترتيب وسجل العمليات"),
    ("Shop", "المتجر", "🛒", "شراء الرتب والمكافآت بالنقاط"),
    ("Games", "الألعاب والفعاليات", "🎮", "العب واجمع النقاط"),
    ("Music", "الموسيقى", "🎵", "تشغيل الأغاني في الرومات الصوتية"),
    ("Info", "المعلومات", "ℹ️", "معلومات الأعضاء والسيرفر"),
    ("Moderation", "الإدارة", "🔨", "التحذير، الكتم، الطرد والحظر"),
    ("Panel", "لوحة التحكم", "🧭", "كل إعدادات البوت بالأزرار والقوائم (للإدارة)"),
    ("CmdAdmin", "الأوامر والاختصارات", "🧩", "استعراض الأوامر وتعطيلها واختصاراتها النصية (للمسؤولين)"),
    ("Settings", "الإعدادات", "⚙️", "إعدادات البوت والصلاحيات بالأوامر (للمسؤولين)"),
]


def walk(cmds):
    for c in cmds:
        if isinstance(c, app_commands.Group):
            yield from walk(c.commands)
        else:
            yield c


def usage(cmd: app_commands.Command) -> str:
    params = " ".join(f"<{p.display_name}>" if p.required else f"[{p.display_name}]" for p in cmd.parameters)
    return f"`/{cmd.qualified_name}{(' ' + params) if params else ''}`"


class HelpView(discord.ui.View):
    def __init__(self, bot, owner_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.owner_id = owner_id
        options = [discord.SelectOption(label="الرئيسية", emoji="🏠", value="home")]
        options += [discord.SelectOption(label=t, emoji=i, description=d, value=c) for c, t, i, d in HELP_SECTIONS]
        self.select = discord.ui.Select(placeholder="اختر قسمًا...", options=options)
        self.select.callback = self.on_select
        self.add_item(self.select)

    def home(self, guild: Optional[discord.Guild]) -> discord.Embed:
        e = ui.info("📖 قائمة أوامر البوت",
                    "مرحبًا! اختر قسمًا من القائمة بالأسفل لعرض أوامره وطريقة استخدامها.\n\n"
                    "`<>` = مطلوب • `[]` = اختياري")
        for cog, title, icon, desc in HELP_SECTIONS:
            n = len(list(walk(self.bot.get_cog(cog).__cog_app_commands__))) if self.bot.get_cog(cog) else 0
            e.add_field(name=f"{icon} {title}", value=f"{desc}\n`{n}` أمر")
        if self.bot.user:
            e.set_thumbnail(url=self.bot.user.display_avatar.url)
        return ui.brand(e, guild)

    def section(self, cog_name: str) -> discord.Embed:
        _, title, icon, desc = next(s for s in HELP_SECTIONS if s[0] == cog_name)
        cog = self.bot.get_cog(cog_name)
        lines = [f"{usage(c)}\n└ {c.description}" for c in walk(cog.__cog_app_commands__)] if cog else []
        e = ui.info(f"{icon} {title}", desc + "\n\n" + "\n".join(lines))
        if len(e.description) > 4000:
            e.description = e.description[:3990] + "…"
        return e

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("استخدم `/help` بنفسك 😊", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        value = self.select.values[0]
        embed = self.home(interaction.guild) if value == "home" else self.section(value)
        await interaction.response.edit_message(embed=embed, view=self)


class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.started = time.time()

    @app_commands.command(name="help", description="قائمة جميع أوامر البوت وطريقة استخدامها")
    async def help(self, interaction: discord.Interaction):
        view = HelpView(self.bot, interaction.user.id)
        await ui.reply(interaction, view.home(interaction.guild), view=view)

    @app_commands.command(name="userinfo", description="معلومات عضو")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        m = member or interaction.user
        s = await self.bot.db.get_settings(interaction.guild_id)
        w = await self.bot.db.wallet(interaction.guild_id, m.id)
        warns = len(await self.bot.db.warnings(interaction.guild_id, m.id))
        if s["info_cards"] and cards.available() and not m.bot:
            try:
                if not interaction.response.is_done():
                    await interaction.response.defer()
                top = next((r for r in reversed(m.roles) if not r.is_default()), None)
                png = await asyncio.to_thread(
                    cards.render_profile, name=m.display_name, tag=f"@{m.name}", avatar=await images.avatar(m),
                    balance=w["balance"], currency=s["currency_name"], rank=w["rank"], games_won=w["games_won"],
                    total_earned=w["total_earned"], warnings=warns, roles=max(0, len(m.roles) - 1),
                    joined=m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "—",
                    created=m.created_at.strftime("%Y-%m-%d"), top_role=top.name if top else "",
                    guild_name=interaction.guild.name)
                return await ui.reply_card(interaction, png, "profile.png",
                                           f"الملف الشخصي لـ {m.display_name}: رصيد {w['balance']:,}، ترتيب {w['rank']}",
                                           in_embed=s["game_image_in_embed"])
            except Exception:  # noqa: BLE001
                log.exception("Profile card failed; using text fallback")
        e = ui.info(f"👤 {m.display_name}", f"{m.mention} • `{m}`", m.color.value or ui.Colors.PRIMARY)
        e.set_thumbnail(url=m.display_avatar.url)
        e.add_field(name="🆔 الآيدي", value=f"`{m.id}`")
        e.add_field(name="📅 إنشاء الحساب", value=ts(m.created_at, "D"))
        e.add_field(name="📥 دخل السيرفر", value=ts(m.joined_at, "D") if m.joined_at else "—")
        e.add_field(name=f"{s['currency_emoji']} الرصيد", value=f"`{w['balance']:,}` • ترتيب `#{w['rank']}`")
        e.add_field(name="🎮 فوز بالألعاب", value=f"`{w['games_won']:,}`")
        e.add_field(name="⚠️ التحذيرات", value=f"`{warns}`")
        roles = [r.mention for r in reversed(m.roles) if not r.is_default()]
        e.add_field(name=f"🎖️ الرتب ({len(roles)})", value=" ".join(roles[:20]) or "—", inline=False)
        if m.is_timed_out():
            e.add_field(name="🔇 مكتوم حتى", value=ts(m.timed_out_until))
        if m.banner:
            e.set_image(url=m.banner.url)
        await ui.reply(interaction, ui.brand(e, interaction.guild))

    @app_commands.command(name="serverinfo", description="معلومات السيرفر")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        humans = sum(1 for m in g.members if not m.bot)
        e = ui.info(f"🏠 {g.name}", g.description or None)
        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.url)
        e.add_field(name="👑 المالك", value=f"<@{g.owner_id}>")
        e.add_field(name="📅 الإنشاء", value=ts(g.created_at, "D"))
        e.add_field(name="🆔 الآيدي", value=f"`{g.id}`")
        e.add_field(name="👥 الأعضاء", value=f"`{g.member_count:,}` • 🧑 `{humans:,}` • 🤖 `{g.member_count - humans:,}`")
        e.add_field(name="💬 الرومات", value=f"📝 `{len(g.text_channels)}` • 🔊 `{len(g.voice_channels)}`")
        e.add_field(name="🎖️ الرتب", value=f"`{len(g.roles) - 1}`")
        e.add_field(name="🚀 البوست", value=f"المستوى `{g.premium_tier}` • `{g.premium_subscription_count}` بوست")
        e.add_field(name="🛡️ مستوى التحقق", value=f"`{g.verification_level.name}`")
        e.add_field(name="😀 الإيموجي", value=f"`{len(g.emojis)}`")
        await ui.reply(interaction, e)

    @app_commands.command(name="avatar", description="عرض صورة عضو")
    async def avatar(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        m = member or interaction.user
        e = ui.info(f"🖼️ صورة {m.display_name}")
        e.set_image(url=m.display_avatar.with_size(1024).url)
        await ui.reply(interaction, e)

    @app_commands.command(name="ping", description="سرعة استجابة البوت")
    async def ping(self, interaction: discord.Interaction):
        uptime = int(time.time() - self.started)
        h, rem = divmod(uptime, 3600)
        e = ui.info("🏓 Pong!", color=ui.Colors.SUCCESS)
        e.add_field(name="الاستجابة", value=f"`{self.bot.latency * 1000:.0f}ms`")
        e.add_field(name="مدة التشغيل", value=f"`{h}س {rem // 60}د`")
        e.add_field(name="السيرفرات", value=f"`{len(self.bot.guilds)}`")
        await ui.reply(interaction, e)


async def setup(bot):
    await bot.add_cog(Info(bot))

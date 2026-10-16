"""لوحة إعدادات البوت والصلاحيات — كل شيء قابل للتعديل من الإدارة."""
from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot import ui
from bot.checks import admin_only
from bot.settings_schema import PERMISSION_CATEGORIES, SETTINGS, SETTINGS_BY_KEY, format_value, parse_value

CHANNEL_KEYS = {
    "السجل العام": "log_channel",
    "سجل الإدارة": "mod_log_channel",
    "سجل الاقتصاد": "economy_log_channel",
    "روم الألعاب": "games_channel",
    "الصوت الدائم": "voice_home_channel",
    "إشعارات الشراء": "shop_notify_channel",
}


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    _admins = discord.Permissions(administrator=True)  # تُخفي المجموعتين عن غير المسؤولين في قائمة ديسكورد
    settings = app_commands.Group(name="settings", description="إعدادات البوت (للمسؤولين)", guild_only=True,
                                  default_permissions=_admins)
    perms = app_commands.Group(name="permissions", description="تحديد الرتب المسموح لها بكل فئة أوامر",
                               guild_only=True, default_permissions=_admins)

    # ------------------------------------------------------------------ عرض
    @settings.command(name="view", description="عرض كل إعدادات البوت الحالية")
    @admin_only()
    async def view(self, interaction: discord.Interaction):
        values = await self.bot.db.get_settings(interaction.guild_id)
        groups: dict[str, list[str]] = {}
        for s in SETTINGS:
            groups.setdefault(s.group, []).append(f"**{s.label}**\n└ {format_value(s, values[s.key])}  ·  `{s.key}`")
        pages = []
        for group, lines in groups.items():
            e = ui.info(f"⚙️ الإعدادات — {group}", "\n".join(lines))
            e.set_footer(text="للتعديل بسهولة: /panel  •  أو /settings set")
            pages.append(e)
        await ui.Paginator(pages, interaction.user.id).start(interaction, ephemeral=True)

    # ------------------------------------------------------------------ تعديل
    @settings.command(name="set", description="تعديل إعداد (مكافآت الألعاب، الحدود، الحماية...)")
    @app_commands.describe(key="الإعداد المراد تعديله", value="القيمة الجديدة (للتفعيل: نعم / لا)")
    @admin_only()
    async def set_(self, interaction: discord.Interaction, key: str, value: str):
        s = SETTINGS_BY_KEY.get(key)
        if not s:
            return await ui.reply(interaction, ui.error("إعداد غير معروف. اختر من القائمة."), ephemeral=True)
        try:
            parsed = self._parse(s, value, interaction.guild)
        except ValueError as exc:
            return await ui.reply(interaction, ui.error(str(exc)), ephemeral=True)
        await self.bot.db.set_setting(interaction.guild_id, key, parsed)
        await ui.reply(interaction, ui.success("تم حفظ الإعداد", f"**{s.label}** ← {format_value(s, parsed)}"),
                       ephemeral=True)
        e = ui.info("⚙️ تعديل إعداد", f"{interaction.user.mention} غيّر **{s.label}** إلى {format_value(s, parsed)}",
                    ui.Colors.LOG)
        await self.bot.send_log(interaction.guild, "log", e)

    @staticmethod
    def _parse(s, value: str, guild: discord.Guild):
        if s.kind == "channel":
            raise ValueError("استخدم `/settings channel` أو `/panel` لاختيار الروم.")
        return parse_value(s, value, lambda rid: guild.get_role(rid) is not None)

    @set_.autocomplete("key")
    async def key_autocomplete(self, _interaction: discord.Interaction, current: str):
        current = current.lower()
        items = [s for s in SETTINGS if s.kind != "channel"
                 and (current in s.label.lower() or current in s.key or current in s.group)]
        return [app_commands.Choice(name=f"{s.group} • {s.label}"[:100], value=s.key) for s in items[:25]]

    @settings.command(name="reset", description="إرجاع إعداد لقيمته الافتراضية")
    @admin_only()
    async def reset(self, interaction: discord.Interaction, key: str):
        s = SETTINGS_BY_KEY.get(key)
        if not s:
            return await ui.reply(interaction, ui.error("إعداد غير معروف."), ephemeral=True)
        await self.bot.db.reset_setting(interaction.guild_id, key)
        await ui.reply(interaction, ui.success("تمت الإعادة", f"**{s.label}** ← {format_value(s, s.default)}"),
                       ephemeral=True)

    @reset.autocomplete("key")
    async def reset_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [app_commands.Choice(name=f"{s.group} • {s.label}"[:100], value=s.key)
                for s in SETTINGS if current in s.label.lower() or current in s.key][:25]

    @settings.command(name="channel", description="تحديد رومات السجلات والألعاب")
    @app_commands.describe(kind="نوع الروم (الصوت الدائم = روم يبقى فيه البوت 24/7)", channel="الروم (اتركه فارغًا للإلغاء)")
    @admin_only()
    async def channel(self, interaction: discord.Interaction,
                      kind: Literal["السجل العام", "سجل الإدارة", "سجل الاقتصاد", "روم الألعاب", "الصوت الدائم",
                            "إشعارات الشراء"],
                      channel: discord.TextChannel | discord.VoiceChannel | discord.StageChannel | None = None):
        key = CHANNEL_KEYS[kind]
        if channel:
            is_voice = isinstance(channel, (discord.VoiceChannel, discord.StageChannel))
            if (key == "voice_home_channel") != is_voice:
                return await ui.reply(interaction, ui.error(
                    "«الصوت الدائم» يحتاج روم صوتيًا، وباقي الأنواع تحتاج روم نصيًا."), ephemeral=True)
            perms = channel.permissions_for(interaction.guild.me)
            if is_voice:
                if not (perms.view_channel and perms.connect and perms.speak):
                    return await ui.reply(interaction, ui.error(
                        f"لا أستطيع الاتصال والتحدث في {channel.mention}. امنحني صلاحيات العرض والاتصال والتحدث."),
                        ephemeral=True)
            elif not (perms.send_messages and perms.embed_links and perms.view_channel):
                return await ui.reply(interaction, ui.error(f"لا أستطيع الإرسال في {channel.mention}. "
                                                            "تأكد من صلاحيات العرض والإرسال والتضمين."),
                                      ephemeral=True)
            await self.bot.db.set_setting(interaction.guild_id, key, channel.id)
            msg = f"**{kind}** ← {channel.mention}"
        else:
            await self.bot.db.reset_setting(interaction.guild_id, key)
            msg = f"تم إلغاء تحديد **{kind}**."
        await ui.reply(interaction, ui.success("تم الحفظ", msg), ephemeral=True)

    # ------------------------------------------------------------------ الصلاحيات
    _cat_choices = [app_commands.Choice(name=v[0][:100], value=k) for k, v in PERMISSION_CATEGORIES.items()]

    @perms.command(name="add", description="السماح لرتبة باستخدام فئة أوامر")
    @app_commands.describe(category="فئة الأوامر", role="الرتبة")
    @app_commands.choices(category=_cat_choices)
    @admin_only()
    async def perm_add(self, interaction: discord.Interaction, category: app_commands.Choice[str], role: discord.Role):
        await self.bot.db.add_perm_role(interaction.guild_id, category.value, role.id)
        await ui.reply(interaction, ui.success("تمت إضافة الصلاحية",
                                               f"{role.mention} يمكنه الآن استخدام **{category.name}**."),
                       ephemeral=True)

    @perms.command(name="remove", description="سحب صلاحية فئة أوامر من رتبة")
    @app_commands.choices(category=_cat_choices)
    @admin_only()
    async def perm_remove(self, interaction: discord.Interaction, category: app_commands.Choice[str],
                          role: discord.Role):
        await self.bot.db.remove_perm_role(interaction.guild_id, category.value, role.id)
        await ui.reply(interaction, ui.success("تم سحب الصلاحية", f"{role.mention} ← **{category.name}**"),
                       ephemeral=True)

    @perms.command(name="list", description="عرض الرتب المسموح لها بكل فئة")
    @admin_only()
    async def perm_list(self, interaction: discord.Interaction):
        e = ui.info("🔐 صلاحيات الأوامر",
                    "إذا لم تُحدد رتب لفئة، يُعتمد على صلاحية ديسكورد الافتراضية. المسؤولون (Administrator) "
                    "يملكون كل الصلاحيات دائمًا.")
        for key, (label, fallback) in PERMISSION_CATEGORIES.items():
            roles = await self.bot.db.perm_roles(interaction.guild_id, key)
            value = " ".join(f"<@&{r}>" for r in roles) if roles else f"افتراضي: `{fallback}`"
            e.add_field(name=label, value=value, inline=False)
        await ui.reply(interaction, e, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Settings(bot))

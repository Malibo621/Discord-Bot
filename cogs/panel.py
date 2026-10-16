"""لوحة التحكم الإدارية /panel — كل إعدادات البوت من مكان واحد بالقوائم والأزرار والنوافذ.

الأمان:
- اللوحة مخفية (ephemeral) ومقيّدة بصاحبها وبالسيرفر الذي فُتحت فيه.
- كل عملية (ضغطة زر، اختيار من قائمة، إرسال نافذة) تعيد فحص الصلاحية من الخادم بالرتب الحالية،
  فإخفاء الأزرار ليس خط الدفاع الوحيد، وسحب رتبة من مسؤول أثناء فتح اللوحة يوقفه فورًا.
- كل الكتابة في قاعدة البيانات تستخدم interaction.guild_id، ولا تُقرأ معرّفات سيرفر من التفاعل.

التوسّع: أي إعداد جديد يُضاف في bot/settings_schema.py يظهر تلقائيًا في قسمه حسب group،
وأي مجموعة جديدة غير مربوطة بقسم تحصل على قسم خاص بها تلقائيًا.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import ui
from bot.checks import NoPermission, allowed
from bot.settings_schema import PERMISSION_CATEGORIES, SETTINGS, SETTINGS_BY_KEY, Setting, format_value, parse_value
from cogs.economy import POINT_ACTIONS, apply_points_change
from cogs.games import GAMES, add_custom_question, add_custom_word, content_embed
from cogs.shop import deliver_purchase, refund_purchase, role_sale_error

ADMIN = "admin"
SETTING_PERM = ADMIN  # تعديل الإعدادات العامة للمسؤولين فقط (مثل /settings)


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    emoji: str
    about: str
    perm: str                       # من يمكنه فتح القسم
    groups: tuple[str, ...] = ()    # مجموعات الإعدادات المعروضة فيه


_SECTIONS = [
    Section("games", "الألعاب", "🎮", "تفعيل الألعاب، مددها، مكافآتها، والمحتوى المخصص", "events",
            ("الألعاب", "مكافآت الألعاب")),
    Section("economy", "النقاط والمكافآت", "🪙", "العملة، التحويلات، حماية الكسب، وتعديل أرصدة الأعضاء", "economy",
            ("عام", "التحويلات", "مكافحة الاستغلال")),
    Section("shop", "المتجر", "🛒", "المنتجات، الإظهار والإخفاء، والطلبات المعلّقة", "shop"),
    Section("channels", "القنوات والسجلات", "📢", "رومات السجلات وروم الألعاب", ADMIN, ("القنوات",)),
    Section("protection", "الحماية والإدارة", "🛡️", "السبام، الروابط، التخريب، والعقوبات التلقائية", ADMIN,
            ("الحماية", "الإدارة")),
    Section("permissions", "الصلاحيات", "🔐", "ربط فئات الأوامر برتب السيرفر", ADMIN),
]


def _with_fallback(sections: list[Section]) -> list[Section]:
    mapped = {g for s in sections for g in s.groups}
    extra = [g for g in dict.fromkeys(st.group for st in SETTINGS) if g not in mapped]
    return sections + [Section(f"group:{g}", g, "⚙️", f"إعدادات {g}", ADMIN, (g,)) for g in extra]


SECTIONS = _with_fallback(_SECTIONS)
SECTIONS_BY_KEY = {s.key: s for s in SECTIONS}
PANEL_PERMS = {s.perm for s in SECTIONS}
KIND_EMOJI = {"bool": "🔘", "int": "🔢", "str": "🔤", "channel": "#️⃣"}
PAGE = 25


def section_settings(section: Section) -> list[Setting]:
    return [st for st in SETTINGS if st.group in section.groups]


def plain_value(st: Setting, value) -> str:
    if st.kind == "bool":
        return "مفعّل" if value else "معطّل"
    if st.kind == "channel":
        return "محدد" if value else "غير محدد"
    if st.kind == "int":
        return f"{value:,}"
    return str(value)[:90]


async def visible_sections(db, member) -> list[Section]:
    return [s for s in SECTIONS if await allowed(db, member, s.perm)]


# ====================================================================== اللوحة
class PanelView(ui.BaseView):
    deny_message = "هذه اللوحة خاصة بمن فتحها. استخدم /panel بنفسك."

    def __init__(self, bot, owner: discord.Member, guild: discord.Guild):
        super().__init__(owner_id=owner.id, timeout=600)
        self.bot = bot
        self.guild_id = guild.id
        self.section: Optional[Section] = None
        self.mode: Optional[str] = None        # channel | member | perm_roles | item | order | role_item
        self.target = None                     # مفتاح الإعداد/الفئة/رقم المنتج/الطلب/الإجراء
        self.page = 0
        self.status: Optional[tuple[bool, str]] = None
        self.confirm_refund: Optional[int] = None
        self.last_interaction: Optional[discord.Interaction] = None

    # ------------------------------------------------------------------ الأمان
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(embed=ui.error("هذه اللوحة تخص سيرفرًا آخر."), ephemeral=True)
            return False
        return await super().interaction_check(interaction)

    async def guard(self, interaction: discord.Interaction, perm: str) -> bool:
        """فحص من الخادم عند كل عملية — يُستدعى أيضًا من النوافذ المنبثقة."""
        if interaction.guild_id != self.guild_id or interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=ui.error("غير مسموح."), ephemeral=True)
            return False
        if not await allowed(self.bot.db, interaction.user, perm):
            label = "المسؤولين" if perm == ADMIN else f"«{PERMISSION_CATEGORIES[perm][0]}»"
            self.mode = None
            await self.refresh(interaction, (False, f"هذه العملية تتطلب صلاحية {label}، ولا تملكها حاليًا."))
            return False
        return True

    # ------------------------------------------------------------------ العرض
    async def refresh(self, interaction: discord.Interaction, status: Optional[tuple[bool, str]] = None):
        if status is not None:
            self.status = status
        embed = await self.build(interaction.user)
        self.last_interaction = interaction
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if self.last_interaction:
            e = ui.info(f"{ui.Icons.ADMIN}  لوحة التحكم", "انتهت مدة الجلسة. افتح اللوحة من جديد بـ `/panel`.",
                        ui.Colors.LOG)
            try:
                await self.last_interaction.edit_original_response(embed=e, view=None)
            except discord.HTTPException:
                pass

    async def build(self, member) -> discord.Embed:
        db = self.bot.db
        guild = self.bot.get_guild(self.guild_id)
        s = await db.get_settings(self.guild_id)
        sections = await visible_sections(db, member)
        self.clear_items()
        self.add_item(SectionSelect(sections, self.section))

        if self.section is None:
            embed = await self._home(guild, s, sections)
        elif self.section.key == "shop":
            embed = await self._shop(s)
        elif self.section.key == "permissions":
            embed = await self._permissions()
        else:
            embed = await self._settings_section(member, s)

        self.add_item(CloseButton())
        if self.status:
            ok, text = self.status
            embed.add_field(name=("✅ تمت العملية" if ok else "⛔ لم تتم العملية"), value=text[:1024], inline=False)
            embed.colour = ui.Colors.SUCCESS if ok else ui.Colors.ERROR
            self.status = None
        return ui.brand(embed, guild, "لوحة التحكم")

    async def _home(self, guild, s, sections) -> discord.Embed:
        db = self.bot.db
        enabled = sum(1 for k in GAMES if s.get(f"game_{k}_enabled", True))
        items = await db.shop_items(self.guild_id, include_inactive=True)
        pending = await db.pending_purchases(self.guild_id)
        e = ui.info(f"{ui.Icons.ADMIN}  لوحة التحكم — {guild.name if guild else ''}",
                    "اختر قسمًا من القائمة بالأسفل. كل تعديل يُحفظ فورًا ويبقى بعد إعادة التشغيل.\n" + ui.LINE,
                    ui.Colors.ADMIN)
        e.add_field(name="🎮 الألعاب", value=f"`{enabled}/{len(GAMES)}` مفعّلة")
        e.add_field(name=f"{s['currency_emoji']} العملة", value=s["currency_name"])
        e.add_field(name="🛒 المتجر", value=f"`{sum(1 for i in items if i['active'])}` منتج  ·  "
                                           f"`{len(pending)}` طلب معلّق")
        e.add_field(name="📋 روم السجل", value=f"<#{s['log_channel']}>" if s["log_channel"] else "`غير محدد`")
        e.add_field(name="⏱️ انتظار الألعاب", value=f"`{s['game_cooldown']}` ث")
        e.add_field(name="📊 حد الكسب اليومي", value=f"`{s['daily_earn_cap']:,}`" if s["daily_earn_cap"] else "`بلا حد`")
        e.add_field(name="الأقسام المتاحة لك",
                    value="\n".join(f"{sec.emoji} **{sec.label}** — {sec.about}" for sec in sections) or "—",
                    inline=False)
        if guild and guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        return e

    async def _settings_section(self, member, s) -> discord.Embed:
        sec = self.section
        sts = section_settings(sec)
        can_edit = await allowed(self.bot.db, member, SETTING_PERM)
        lines, last_group = [], None
        for st in sts:
            if st.group != last_group and len(sec.groups) > 1:
                lines.append(f"\n**{st.group}**")
                last_group = st.group
            lines.append(f"{KIND_EMOJI[st.kind]} {st.label} {ui.ARROW} {format_value(st, s[st.key])}")
        e = ui.info(f"{sec.emoji}  {sec.label}", f"{sec.about}\n" + "\n".join(lines), ui.Colors.ADMIN)
        if len(e.description) > 4000:
            e.description = e.description[:3990] + "…"

        if self.mode == "channel" and self.target in SETTINGS_BY_KEY:
            st = SETTINGS_BY_KEY[self.target]
            e.add_field(name="#️⃣ اختيار روم", value=f"اختر الروم الجديد لـ **{st.label}** من القائمة.", inline=False)
            self.add_item(ChannelPicker(st))
            self.add_item(ActionButton("clear_channel", "إلغاء التحديد", "🧹", discord.ButtonStyle.danger, row=2))
            self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
            return e
        if self.mode == "member":
            e.add_field(name=f"👤 {POINT_ACTIONS[self.target]}", value="اختر العضو من القائمة.", inline=False)
            self.add_item(MemberPicker())
            self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
            return e

        if can_edit and sts:
            pages = max(1, (len(sts) + PAGE - 1) // PAGE)
            self.page = min(self.page, pages - 1)
            self.add_item(SettingSelect(sts[self.page * PAGE:(self.page + 1) * PAGE], s))
            if pages > 1:
                self.add_item(ActionButton("prev_page", "السابق", "◀️", row=4, disabled=self.page == 0))
                self.add_item(ActionButton("next_page", "التالي", "▶️", row=4, disabled=self.page >= pages - 1))
            e.set_footer(text="🔘 اختيار إعداد تفعيل يقلبه فورًا  ·  🔢 يفتح نافذة لإدخال القيمة")
        elif sts:
            e.add_field(name="ℹ️ ملاحظة", value="تعديل هذه الإعدادات للمسؤولين فقط، ويمكنك استخدام الإجراءات أدناه.",
                        inline=False)

        if sec.key == "economy" and await allowed(self.bot.db, member, "economy"):
            self.add_item(ActionButton("points:add", "إضافة نقاط", "➕", discord.ButtonStyle.success, row=2))
            self.add_item(ActionButton("points:remove", "خصم نقاط", "➖", discord.ButtonStyle.danger, row=2))
            self.add_item(ActionButton("points:set", "تحديد رصيد", "✏️", discord.ButtonStyle.primary, row=2))
        if sec.key == "games" and await allowed(self.bot.db, member, "events"):
            self.add_item(ActionButton("add_word", "إضافة كلمة", "➕", discord.ButtonStyle.success, row=2))
            self.add_item(ActionButton("add_question", "إضافة سؤال", "❓", discord.ButtonStyle.success, row=2))
            self.add_item(ActionButton("content", "المحتوى المخصص", "📚", row=2))
        return e

    async def _shop(self, s) -> discord.Embed:
        db = self.bot.db
        items = await db.shop_items(self.guild_id, include_inactive=True)
        pending = await db.pending_purchases(self.guild_id)
        e = ui.info("🛒  المتجر", "إدارة المنتجات والطلبات. التعديلات تظهر للأعضاء فورًا في `/shop`.",
                    ui.Colors.ADMIN)

        if self.mode == "role_item":
            e.add_field(name="🎖️ منتج رتبة جديد", value="اختر الرتبة التي سيحصل عليها المشتري.", inline=False)
            self.add_item(RolePicker(max_values=1, placeholder="🎖️ اختر رتبة المنتج..."))
            self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
            return e
        if self.mode == "item":
            item = await db.shop_item(self.guild_id, self.target)
            if item:
                state = "🟢 معروض" if item["active"] else "🙈 مخفي"
                stock = "غير محدود" if item["stock"] < 0 else f"{item['stock']:,}"
                kind = f"🎖️ رتبة <@&{item['role_id']}>" if item["kind"] == "role" else "🎁 مكافأة خاصة"
                e.add_field(name=f"`#{item['id']}` {item['name']}", value=ui.kv([
                    ("السعر", ui.money(item["price"], s)), ("النوع", kind), ("المخزون", stock), ("الحالة", state),
                ]) + (f"\n> {item['description']}" if item["description"] else ""), inline=False)
                self.add_item(ActionButton("edit_item", "تعديل", "✏️", discord.ButtonStyle.primary, row=2))
                self.add_item(ActionButton("toggle_item", "إخفاء" if item["active"] else "إظهار",
                                           "🙈" if item["active"] else "👁️", row=2))
                self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
                return e
            self.mode = None
        if self.mode == "order":
            p = await db.purchase_row(self.guild_id, self.target)
            if p and p["status"] == "pending":
                e.add_field(name=f"🟡 طلب `#{p['id']}`", value=ui.kv([
                    ("العضو", f"<@{p['user_id']}>"), ("المنتج", p["item_name"]), ("السعر", ui.money(p["price"], s)),
                    ("التاريخ", f"<t:{p['created_at']}:R>"),
                ]), inline=False)
                confirm = self.confirm_refund == p["id"]
                self.add_item(ActionButton("deliver", "تأكيد التسليم", "📦", discord.ButtonStyle.success, row=2))
                self.add_item(ActionButton("refund", "اضغط مجددًا للتأكيد" if confirm else "استرجاع النقاط",
                                           "⚠️" if confirm else "↩️", discord.ButtonStyle.danger, row=2))
                self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
                return e
            self.mode = None

        lines = []
        for i in items[:15]:
            flag = "" if i["active"] else "  🙈"
            lines.append(f"`#{i['id']}` **{i['name']}** — `{i['price']:,}` {s['currency_emoji']}{flag}")
        e.add_field(name=f"📦 المنتجات ({len(items)})", value="\n".join(lines) or "*لا توجد منتجات بعد*", inline=False)
        e.add_field(name=f"🟡 طلبات معلّقة ({len(pending)})",
                    value="\n".join(f"`#{p['id']}` <@{p['user_id']}> — {p['item_name']}" for p in pending[:8])
                    or "*لا توجد طلبات*", inline=False)
        if items:
            self.add_item(ItemSelect(items[:25], s))
        if pending:
            self.add_item(OrderSelect(pending[:25]))
        self.add_item(ActionButton("new_custom", "مكافأة خاصة", "🎁", discord.ButtonStyle.success, row=3))
        self.add_item(ActionButton("new_role", "منتج رتبة", "🎖️", discord.ButtonStyle.success, row=3))
        return e

    async def _permissions(self) -> discord.Embed:
        db = self.bot.db
        e = ui.info("🔐  الصلاحيات",
                    "حدد الرتب المسموح لها بكل فئة أوامر. إن لم تُحدد رتب، تُستخدم صلاحية ديسكورد الافتراضية.\n"
                    "المسؤولون (Administrator) والمالك يملكون كل الصلاحيات دائمًا.", ui.Colors.ADMIN)
        for key, (label, fallback) in PERMISSION_CATEGORIES.items():
            roles = await db.perm_roles(self.guild_id, key)
            value = " ".join(f"<@&{r}>" for r in roles) if roles else f"افتراضي: `{fallback}`"
            e.add_field(name=label, value=value, inline=False)
        if self.mode == "perm_roles" and self.target in PERMISSION_CATEGORIES:
            current = await db.perm_roles(self.guild_id, self.target)
            e.add_field(name="✏️ تعديل", value=f"اختر رتب **{PERMISSION_CATEGORIES[self.target][0]}** "
                                              "(حتى 10 رتب). الاختيار يستبدل القائمة الحالية.", inline=False)
            self.add_item(RolePicker(max_values=10, min_values=0, defaults=current,
                                     placeholder="🔐 اختر الرتب..."))
            self.add_item(ActionButton("clear_perm", "الرجوع للافتراضي", "🧹", discord.ButtonStyle.danger, row=2))
            self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
        else:
            self.add_item(PermCategorySelect())
        return e

    # ------------------------------------------------------------------ الإجراءات
    async def log_setting(self, interaction: discord.Interaction, st: Setting, value) -> None:
        e = ui.info("⚙️ تعديل إعداد", f"{interaction.user.mention} غيّر **{st.label}** إلى {format_value(st, value)}"
                                     " (من لوحة التحكم)", ui.Colors.LOG)
        await self.bot.send_log(interaction.guild, "log", e)

    async def save_setting(self, interaction: discord.Interaction, st: Setting, value) -> None:
        if value is None:
            await self.bot.db.reset_setting(self.guild_id, st.key)
            value = st.default
        else:
            await self.bot.db.set_setting(self.guild_id, st.key, value)
        await self.log_setting(interaction, st, value)
        self.mode = None
        await self.refresh(interaction, (True, f"**{st.label}** {ui.ARROW} {format_value(st, value)}"))

    async def on_action(self, interaction: discord.Interaction, action: str):
        db = self.bot.db
        if action == "back":
            self.mode, self.target, self.confirm_refund = None, None, None
            return await self.refresh(interaction)
        if action in ("prev_page", "next_page"):
            self.page += -1 if action == "prev_page" else 1
            return await self.refresh(interaction)
        if action == "clear_channel":
            if not await self.guard(interaction, SETTING_PERM) or self.target not in SETTINGS_BY_KEY:
                return
            return await self.save_setting(interaction, SETTINGS_BY_KEY[self.target], None)
        if action.startswith("points:"):
            if not await self.guard(interaction, "economy"):
                return
            self.mode, self.target = "member", action.split(":", 1)[1]
            return await self.refresh(interaction)
        if action == "add_word":
            if await self.guard(interaction, "events"):
                await interaction.response.send_modal(WordModal(self))
            return
        if action == "add_question":
            if await self.guard(interaction, "events"):
                await interaction.response.send_modal(QuestionModal(self))
            return
        if action == "content":
            if await self.guard(interaction, "events"):
                await interaction.response.send_message(embed=await content_embed(self.bot, self.guild_id),
                                                        ephemeral=True)
            return

        # ---- المتجر
        if not await self.guard(interaction, "shop"):
            return
        if action == "new_custom":
            return await interaction.response.send_modal(ItemModal(self, kind="custom"))
        if action == "new_role":
            self.mode = "role_item"
            return await self.refresh(interaction)
        if action == "edit_item":
            item = await db.shop_item(self.guild_id, self.target)
            if not item:
                return await self.refresh(interaction, (False, "المنتج غير موجود."))
            return await interaction.response.send_modal(ItemModal(self, kind=item["kind"], item=item))
        if action == "toggle_item":
            item = await db.shop_item(self.guild_id, self.target)
            if not item:
                return await self.refresh(interaction, (False, "المنتج غير موجود."))
            await db.update_shop_item(self.guild_id, item["id"], active=0 if item["active"] else 1)
            state = "مخفي الآن" if item["active"] else "معروض الآن"
            return await self.refresh(interaction, (True, f"**{item['name']}** {state} في المتجر."))
        if action == "deliver":
            ok, msg = await deliver_purchase(self.bot, interaction.guild, interaction.user, self.target)
            self.mode = None
            return await self.refresh(interaction, (ok, msg))
        if action == "refund":
            if self.confirm_refund != self.target:  # خطوة تأكيد ثانية قبل إعادة النقاط
                self.confirm_refund = self.target
                return await self.refresh(interaction)
            self.confirm_refund = None
            ok, msg = await refund_purchase(self.bot, interaction.guild, interaction.user, self.target)
            self.mode = None
            return await self.refresh(interaction, (ok, msg))


# ====================================================================== المكوّنات
class SectionSelect(discord.ui.Select):
    def __init__(self, sections: list[Section], current: Optional[Section]):
        options = [discord.SelectOption(label="الرئيسية", value="home", emoji="🏠", default=current is None)]
        options += [discord.SelectOption(label=s.label, value=s.key, emoji=s.emoji, description=s.about[:100],
                                         default=current is not None and current.key == s.key) for s in sections]
        super().__init__(placeholder="📂 اختر قسمًا...", options=options[:25], row=0)

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        value = self.values[0]
        view.mode, view.target, view.page, view.confirm_refund = None, None, 0, None
        if value == "home":
            view.section = None
            return await view.refresh(interaction)
        sec = SECTIONS_BY_KEY.get(value)
        if sec is None or not await view.guard(interaction, sec.perm):
            return
        view.section = sec
        await view.refresh(interaction)


class SettingSelect(discord.ui.Select):
    def __init__(self, settings: list[Setting], values: dict):
        options = [discord.SelectOption(label=st.label[:100], value=st.key, emoji=KIND_EMOJI[st.kind],
                                        description=f"الحالي: {plain_value(st, values[st.key])}"[:100])
                   for st in settings]
        super().__init__(placeholder="⚙️ اختر إعدادًا لتعديله...", options=options, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        st = SETTINGS_BY_KEY.get(self.values[0])
        # التحقق أن الإعداد يتبع القسم المفتوح فعلًا (لا يُوثق بأي قيمة قادمة من العميل)
        if st is None or view.section is None or st.group not in view.section.groups:
            return await view.refresh(interaction, (False, "إعداد غير معروف."))
        if not await view.guard(interaction, SETTING_PERM):
            return
        current = (await view.bot.db.get_settings(view.guild_id))[st.key]
        if st.kind == "bool":
            return await view.save_setting(interaction, st, not current)
        if st.kind == "channel":
            view.mode, view.target = "channel", st.key
            return await view.refresh(interaction)
        await interaction.response.send_modal(SettingModal(view, st, current))


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, st: Setting):
        super().__init__(placeholder=f"#️⃣ {st.label}"[:100], row=1,
                         channel_types=[discord.ChannelType.text, discord.ChannelType.news])
        self.st = st

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, SETTING_PERM):
            return
        channel = interaction.guild.get_channel(self.values[0].id)
        if channel is None:
            return await view.refresh(interaction, (False, "الروم غير موجود في هذا السيرفر."))
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            return await view.refresh(interaction, (False, f"لا أستطيع الإرسال في {channel.mention}. "
                                                           "امنحني صلاحيات العرض والإرسال والتضمين."))
        await view.save_setting(interaction, self.st, channel.id)


class MemberPicker(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="👤 اختر العضو...", row=1)

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, "economy"):
            return
        member = interaction.guild.get_member(self.values[0].id)
        if member is None:
            return await view.refresh(interaction, (False, "العضو ليس في السيرفر."))
        if member.bot:
            return await view.refresh(interaction, (False, "لا يمكن تعديل نقاط البوتات."))
        await interaction.response.send_modal(PointsModal(view, view.target, member))


class RolePicker(discord.ui.RoleSelect):
    def __init__(self, *, max_values: int, min_values: int = 1, defaults: Optional[list[int]] = None,
                 placeholder: str):
        kwargs = {}
        if defaults:
            kwargs["default_values"] = [discord.SelectDefaultValue(id=r, type=discord.SelectDefaultValueType.role)
                                        for r in defaults[:max_values]]
        super().__init__(placeholder=placeholder, min_values=min_values, max_values=max_values, row=1, **kwargs)

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        guild = interaction.guild
        roles = [guild.get_role(r.id) for r in self.values]
        roles = [r for r in roles if r is not None]
        if view.mode == "role_item":
            if not await view.guard(interaction, "shop"):
                return
            role = roles[0] if roles else None
            problem = role_sale_error(role, guild) if role else "اختر رتبة."
            if problem:
                return await view.refresh(interaction, (False, problem))
            return await interaction.response.send_modal(ItemModal(view, kind="role", role=role))
        if view.mode == "perm_roles":
            if not await view.guard(interaction, ADMIN):
                return
            if any(r.is_default() for r in roles):
                return await view.refresh(interaction, (False, "لا يمكن منح صلاحية لرتبة @everyone."))
            category = view.target
            await view.bot.db.set_perm_roles(view.guild_id, category, [r.id for r in roles])
            await view.bot.send_log(guild, "log", ui.info(
                "🔐 تعديل صلاحيات", f"{interaction.user.mention} حدّث رتب **{PERMISSION_CATEGORIES[category][0]}**: "
                + (" ".join(r.mention for r in roles) or "الافتراضي"), ui.Colors.LOG))
            view.mode = None
            return await view.refresh(interaction, (True, f"**{PERMISSION_CATEGORIES[category][0]}** {ui.ARROW} "
                                                          + (" ".join(r.mention for r in roles) or "الافتراضي")))


class PermCategorySelect(discord.ui.Select):
    def __init__(self):
        super().__init__(placeholder="🔐 اختر فئة لتعديل رتبها...", row=1, options=[
            discord.SelectOption(label=label[:100], value=key) for key, (label, _f) in PERMISSION_CATEGORIES.items()])

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, ADMIN) or self.values[0] not in PERMISSION_CATEGORIES:
            return
        view.mode, view.target = "perm_roles", self.values[0]
        await view.refresh(interaction)


class ItemSelect(discord.ui.Select):
    def __init__(self, items, s):
        super().__init__(placeholder="📦 اختر منتجًا لإدارته...", row=1, options=[
            discord.SelectOption(label=f"#{i['id']} {i['name']}"[:100], value=str(i["id"]),
                                 emoji="🟢" if i["active"] else "🙈",
                                 description=f"{i['price']:,} {s['currency_name']}"[:100]) for i in items])

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, "shop"):
            return
        view.mode, view.target = "item", int(self.values[0])
        await view.refresh(interaction)


class OrderSelect(discord.ui.Select):
    def __init__(self, orders):
        super().__init__(placeholder="🟡 اختر طلبًا معلّقًا...", row=2, options=[
            discord.SelectOption(label=f"#{p['id']} {p['item_name']}"[:100], value=str(p["id"])) for p in orders])

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, "shop"):
            return
        view.mode, view.target, view.confirm_refund = "order", int(self.values[0]), None
        await view.refresh(interaction)


class ActionButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary, *, row: int,
                 disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.on_action(interaction, self.action)


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="إغلاق", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        view.stop()
        await interaction.response.edit_message(
            embed=ui.info(f"{ui.Icons.ADMIN}  تم إغلاق لوحة التحكم", "افتحها في أي وقت بـ `/panel`.", ui.Colors.LOG),
            view=None)


# ====================================================================== النوافذ المنبثقة
class PanelModal(discord.ui.Modal):
    perm = ADMIN

    def __init__(self, view: PanelView, title: str):
        super().__init__(title=title[:45], timeout=300)
        self.panel = view

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.guard(interaction, self.perm):
            return
        try:
            result = await self.apply(interaction)
        except ValueError as exc:
            return await self.panel.refresh(interaction, (False, str(exc)))
        if result is not None:
            await self.panel.refresh(interaction, result)

    async def apply(self, interaction: discord.Interaction) -> Optional[tuple[bool, str]]:
        raise NotImplementedError

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        ui.log.exception("Panel modal error", exc_info=error)
        await ui.reply(interaction, ui.error("حدث خطأ غير متوقع أثناء الحفظ."), ephemeral=True)


class SettingModal(PanelModal):
    perm = SETTING_PERM

    def __init__(self, view: PanelView, st: Setting, current):
        super().__init__(view, st.label)
        self.st = st
        hint = (f"من {st.min:,} إلى {st.max:,}" if st.kind == "int" and st.min is not None else "نص حتى 200 حرف")
        self.value = discord.ui.TextInput(label="القيمة الجديدة", default=str(current), required=False,
                                          max_length=200, placeholder=f"{hint} — اتركه فارغًا للقيمة الافتراضية"[:100])
        self.add_item(self.value)

    async def apply(self, interaction):
        raw = self.value.value.strip()
        await self.panel.save_setting(interaction, self.st, parse_value(self.st, raw) if raw else None)


class PointsModal(PanelModal):
    perm = "economy"

    def __init__(self, view: PanelView, action: str, member: discord.Member):
        super().__init__(view, f"{POINT_ACTIONS[action]} — {member.display_name}")
        self.action, self.member = action, member
        self.amount = discord.ui.TextInput(label="المبلغ", placeholder="مثال: 500", max_length=12)
        self.reason = discord.ui.TextInput(label="السبب (اختياري)", required=False, max_length=200,
                                           style=discord.TextStyle.paragraph)
        self.add_item(self.amount)
        self.add_item(self.reason)

    async def apply(self, interaction):
        amount = _int(self.amount.value, "المبلغ", 0 if self.action == "set" else 1, 100_000_000)
        ok, embed = await apply_points_change(self.panel.bot, interaction.guild, interaction.user, self.member,
                                              self.action, amount, self.reason.value)
        self.panel.mode = None
        return ok, embed.description or ""


class WordModal(PanelModal):
    perm = "events"

    def __init__(self, view: PanelView):
        super().__init__(view, "إضافة كلمة للألعاب")
        self.word = discord.ui.TextInput(label="الكلمة", min_length=2, max_length=30)
        self.add_item(self.word)

    async def apply(self, interaction):
        return await add_custom_word(self.panel.bot, self.panel.guild_id, self.word.value, interaction.user.id)


class QuestionModal(PanelModal):
    perm = "events"

    def __init__(self, view: PanelView):
        super().__init__(view, "إضافة سؤال للمسابقة")
        self.q = discord.ui.TextInput(label="السؤال", min_length=5, max_length=200, style=discord.TextStyle.paragraph)
        self.a = discord.ui.TextInput(label="الإجابة الصحيحة", max_length=80)
        self.w = [discord.ui.TextInput(label=f"إجابة خاطئة {i}", max_length=80) for i in (1, 2, 3)]
        for item in (self.q, self.a, *self.w):
            self.add_item(item)

    async def apply(self, interaction):
        return await add_custom_question(self.panel.bot, self.panel.guild_id, self.q.value, self.a.value,
                                         [w.value for w in self.w], interaction.user.id)


class ItemModal(PanelModal):
    perm = "shop"

    def __init__(self, view: PanelView, *, kind: str, role: Optional[discord.Role] = None, item=None):
        title = ("تعديل " + item["name"]) if item else ("منتج رتبة: " + role.name if role else "مكافأة خاصة جديدة")
        super().__init__(view, title)
        self.kind, self.role, self.item = kind, role, item
        self.name = discord.ui.TextInput(label="اسم المنتج", max_length=60,
                                         default=item["name"] if item else (role.name if role else None))
        self.price = discord.ui.TextInput(label="السعر", max_length=12, placeholder="مثال: 2000",
                                          default=str(item["price"]) if item else None)
        self.desc = discord.ui.TextInput(label="الوصف (اختياري)", required=False, max_length=300,
                                         style=discord.TextStyle.paragraph,
                                         default=(item["description"] or "") if item else None)
        self.stock = discord.ui.TextInput(label="الكمية (-1 = غير محدود)", required=False, max_length=8,
                                          default=str(item["stock"]) if item else "-1")
        for x in (self.name, self.price, self.desc, self.stock):
            self.add_item(x)
        self.hours = None
        if kind == "role":
            self.hours = discord.ui.TextInput(label="مدة الرتبة بالساعات (0 = دائمة)", required=False, max_length=6,
                                              default=str(item["duration_hours"]) if item else "0")
            self.add_item(self.hours)

    async def apply(self, interaction):
        db, gid = self.panel.bot.db, self.panel.guild_id
        name = self.name.value.strip()
        if not name:
            raise ValueError("اسم المنتج مطلوب.")
        price = _int(self.price.value, "السعر", 0, 1_000_000_000)
        stock = _int(self.stock.value or "-1", "الكمية", -1, 1_000_000)
        hours = _int(self.hours.value or "0", "المدة", 0, 87600) if self.hours else 0
        desc = self.desc.value.strip() or None
        if self.item:
            await db.update_shop_item(gid, self.item["id"], name=name, price=price, description=desc, stock=stock,
                                      duration_hours=hours if self.kind == "role" else None)
            self.panel.mode = "item"
            return True, f"تم تعديل **{name}**."
        if self.role:  # إعادة الفحص عند الحفظ: ربما تغيّرت رتبة البوت أو صلاحيات الرتبة
            problem = role_sale_error(self.role, interaction.guild)
            if problem:
                raise ValueError(problem)
        item_id = await db.add_shop_item(gid, name, desc, price, self.kind, self.role.id if self.role else None,
                                         stock, hours, True)
        self.panel.mode, self.panel.target = "item", item_id
        await self.panel.bot.send_log(interaction.guild, "economy", ui.info(
            "🛒 منتج جديد", f"{interaction.user.mention} أضاف `#{item_id}` **{name}** بسعر `{price:,}`", ui.Colors.LOG))
        return True, f"أُضيف المنتج `#{item_id}` **{name}** إلى المتجر."


def _int(raw: str, label: str, lo: int, hi: int) -> int:
    try:
        n = int(raw.strip().replace(",", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    except ValueError:
        raise ValueError(f"**{label}** يجب أن يكون رقمًا صحيحًا.") from None
    if not lo <= n <= hi:
        raise ValueError(f"**{label}** يجب أن يكون بين `{lo:,}` و `{hi:,}`.")
    return n


# ====================================================================== الأمر
class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="panel", description="لوحة التحكم الإدارية: الألعاب، النقاط، المتجر، الحماية والصلاحيات")
    @app_commands.guild_only()
    async def panel(self, interaction: discord.Interaction):
        if not await visible_sections(self.bot.db, interaction.user):
            raise NoPermission("admin")
        view = PanelView(self.bot, interaction.user, interaction.guild)
        embed = await view.build(interaction.user)
        await ui.reply(interaction, embed, view=view, ephemeral=True)
        view.last_interaction = interaction


async def setup(bot):
    await bot.add_cog(Panel(bot))

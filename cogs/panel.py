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

from bot import images, ui
from bot.checks import NoPermission, allowed
from bot.settings_schema import (PERMISSION_CATEGORIES, SETTINGS, SETTINGS_BY_KEY, Setting, describe, format_value,
                                 help_for, parse_value)
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
    Section("music", "الصوت والموسيقى", "🎵", "الاتصال الدائم بروم صوتي، ورتب التحكم بالموسيقى", ADMIN,
            ("الصوت والموسيقى",)),
    Section("permissions", "الصلاحيات", "🔐", "ربط فئات الأوامر برتب السيرفر", ADMIN),
    Section("commands", "الأوامر والاختصارات", "🧩", "استعراض الأوامر وتعطيلها وتعديل أوصافها واختصاراتها النصية",
            ADMIN, ("الأوامر والاختصارات",)),
]


# تجميع الأقسام في الصفحة الرئيسية حسب وظيفتها (لا يغيّر الصلاحيات: كل قسم يفحص صلاحيته بنفسه)
CATEGORIES = (
    ("🎮 الفعاليات والاقتصاد", ("games", "economy", "shop")),
    ("🛡️ الحماية والإدارة", ("protection",)),
    ("🔧 السيرفر والبوت", ("channels", "music", "permissions", "commands")),
)


def _with_fallback(sections: list[Section]) -> list[Section]:
    mapped = {g for s in sections for g in s.groups}
    extra = [g for g in dict.fromkeys(st.group for st in SETTINGS) if g not in mapped]
    return sections + [Section(f"group:{g}", g, "⚙️", f"إعدادات {g}", ADMIN, (g,)) for g in extra]


SECTIONS = _with_fallback(_SECTIONS)
SECTIONS_BY_KEY = {s.key: s for s in SECTIONS}
PANEL_PERMS = {s.perm for s in SECTIONS}
KIND_EMOJI = {"bool": "🔘", "int": "🔢", "str": "🔤", "channel": "#️⃣", "role": "🎖️"}
PAGE = 25


def section_settings(section: Section) -> list[Setting]:
    return [st for st in SETTINGS if st.group in section.groups]


def settings_help_pages(section: Section, values: dict, per_page: int = 6) -> list[discord.Embed]:
    """صفحات «شرح الإعدادات»: لكل إعداد اسمه، وظيفته، قيمته الحالية، مداه وافتراضيه، وكيف يُغيَّر."""
    sts = section_settings(section)
    chunks = [sts[i:i + per_page] for i in range(0, len(sts), per_page)] or [[]]
    pages = []
    for n, chunk in enumerate(chunks, 1):
        e = ui.info(f"📖 شرح إعدادات {section.label}", ui.LINE, ui.Colors.ADMIN)
        for st in chunk:
            how = {"bool": "اختره من القائمة ليتبدّل فورًا", "int": "اختره ثم اكتب الرقم في النافذة",
                   "str": "اختره ثم اكتب النص في النافذة", "channel": "اختره ثم اختر الروم",
                   "role": "اختره ثم اختر الرتبة"}[st.kind]
            e.add_field(name=f"{KIND_EMOJI[st.kind]} {st.label}"[:256],
                        value=f"{help_for(st) or '—'}\n**الحالي:** {format_value(st, values[st.key])}\n"
                              f"-# {describe(st)} · {how}"[:1024], inline=False)
        e.set_footer(text=f"صفحة {n} من {len(chunks)}")
        pages.append(e)
    return pages


def plain_value(st: Setting, value) -> str:
    if st.kind == "bool":
        return "مفعّل" if value else "معطّل"
    if st.kind in ("channel", "role"):
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
        self.confirm_delete: Optional[int] = None
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

        if self.section is not None or self.mode is not None:
            self.add_item(ActionButton("home", "الرئيسية", "🏠", row=4))
        self.add_item(CloseButton())
        if self.status:
            ok, text = self.status
            embed.add_field(name=("✅ تمت العملية" if ok else "⛔ لم تتم العملية"), value=text[:1024], inline=False)
            embed.colour = ui.Colors.SUCCESS if ok else ui.Colors.ERROR
            self.status = None
        return ui.brand(embed, guild, "لوحة التحكم")

    async def _attention(self, s, pending) -> list[str]:
        """ما يستحق انتباه المسؤول الآن (إعدادات ناقصة وطلبات معلّقة) — لا شيء شكلي: كلها من الحالة الفعلية."""
        notes = []
        if not s["log_channel"]:
            notes.append("لم يُحدَّد **روم سجل السيرفر** — القنوات والسجلات")
        if not s["mod_log_channel"] and not s["log_channel"]:
            notes.append("لا يوجد **روم سجل للإدارة** (تحذيرات/كتم/حظر) — القنوات والسجلات")
        if pending:
            notes.append(f"**{len(pending)}** طلب معلّق في المتجر ينتظر التسليم — المتجر")
        if not s["games_channel"]:
            notes.append("الألعاب مفتوحة في **كل الرومات** (حدد روم الألعاب إن أردت حصرها)")
        return notes

    async def _home(self, guild, s, sections) -> discord.Embed:
        db = self.bot.db
        enabled = sum(1 for k in GAMES if s.get(f"game_{k}_enabled", True))
        items = await db.shop_items(self.guild_id, include_inactive=True)
        pending = await db.pending_purchases(self.guild_id)
        e = ui.info(f"{ui.Icons.ADMIN}  لوحة التحكم — {guild.name if guild else ''}",
                    "كل إعدادات البوت من هنا. **اختر قسمًا من القائمة بالأسفل** ← ثم الإعداد المطلوب ← يُحفظ فورًا "
                    "ويبقى بعد إعادة التشغيل.\n" + ui.LINE, ui.Colors.ADMIN)
        e.add_field(name="🎮 الألعاب", value=f"`{enabled}/{len(GAMES)}` مفعّلة")
        e.add_field(name=f"{s['currency_emoji']} العملة", value=s["currency_name"])
        e.add_field(name="🛒 المتجر", value=f"`{sum(1 for i in items if i['active'])}` منتج  ·  "
                                           f"`{len(pending)}` طلب معلّق")
        e.add_field(name="📋 روم السجل", value=f"<#{s['log_channel']}>" if s["log_channel"] else "`غير محدد`")
        e.add_field(name="🎵 الاتصال الدائم",
                    value=f"<#{s['voice_home_channel']}>" if s["voice_home_channel"] else "`غير مفعّل`")
        e.add_field(name="📊 حد الكسب اليومي", value=f"`{s['daily_earn_cap']:,}`" if s["daily_earn_cap"] else "`بلا حد`")
        by_key = {sec.key: sec for sec in sections}
        for title, keys in CATEGORIES:
            lines = [f"{by_key[k].emoji} **{by_key[k].label}** — {by_key[k].about}" for k in keys if k in by_key]
            if lines:
                e.add_field(name=title, value="\n".join(lines), inline=False)
        extra = [sec for sec in sections if sec.key not in {k for _t, ks in CATEGORIES for k in ks}]
        if extra:
            e.add_field(name="⚙️ أخرى", value="\n".join(f"{x.emoji} **{x.label}** — {x.about}" for x in extra),
                        inline=False)
        notes = await self._attention(s, pending)
        e.add_field(name="🔔 ما يحتاج انتباهك" if notes else "✅ الحالة",
                    value="\n".join(f"• {n}" for n in notes) if notes else "كل شيء مضبوط.", inline=False)
        e.set_footer(text="تلميح: داخل أي قسم اضغط «شرح الإعدادات» لمعرفة وظيفة كل إعداد وقيمته وطريقة تغييره")
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
            e.add_field(name="#️⃣ اختيار روم", value=f"اختر الروم الجديد لـ **{st.label}** من القائمة.\n"
                                                    f"> {help_for(st)}", inline=False)
            self.add_item(ChannelPicker(st))
            self.add_item(ActionButton("clear_channel", "إلغاء التحديد", "🧹", discord.ButtonStyle.danger, row=2))
            self.add_item(ActionButton("back", "رجوع", "↩️", row=2))
            return e
        if self.mode == "role_setting" and self.target in SETTINGS_BY_KEY:
            st = SETTINGS_BY_KEY[self.target]
            e.add_field(name="🎖️ اختيار رتبة", value=f"اختر الرتبة الجديدة لـ **{st.label}** من القائمة.\n"
                                                      f"> {help_for(st)}", inline=False)
            self.add_item(RolePicker(max_values=1, placeholder=f"🎖️ {st.label}"[:100]))
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
            e.set_footer(text="🔘 تفعيل/تعطيل فوري  ·  🔢 نافذة لإدخال القيمة  ·  #️⃣ 🎖️ قائمة اختيار  ·  "
                              "«شرح الإعدادات» يوضح وظيفة كل إعداد")
            self.add_item(ActionButton("explain", "شرح الإعدادات", "📖", row=2))
        elif sts:
            e.add_field(name="ℹ️ ملاحظة", value="تعديل هذه الإعدادات للمسؤولين فقط، ويمكنك استخدام الإجراءات أدناه.",
                        inline=False)

        if sec.key == "music":
            self.add_item(ActionButton("music_perms", "رتب التحكم بالموسيقى", "🔐", row=2))
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
                rows = [("السعر", ui.money(item["price"], s)), ("النوع", kind), ("المخزون", stock), ("الحالة", state)]
                if item["kind"] == "role":
                    h = item["duration_hours"]
                    rows.append(("المدة", "دائمة" if not h else (f"{h // 24} يوم" if h % 24 == 0 else f"{h} ساعة")))
                e.add_field(name=f"`#{item['id']}` {item['name']}", value=ui.kv(rows)
                            + (f"\n> {item['description']}" if item["description"] else ""), inline=False)
                if item["image_url"]:
                    e.set_image(url=item["image_url"])
                confirm = self.confirm_delete == item["id"]
                self.add_item(ActionButton("edit_item", "تعديل", "✏️", discord.ButtonStyle.primary, row=2))
                self.add_item(ActionButton("stock_item", "المخزون", "📦", row=2))
                self.add_item(ActionButton("toggle_item", "إخفاء" if item["active"] else "إظهار",
                                           "🙈" if item["active"] else "👁️", row=2))
                self.add_item(ActionButton("delete_item", "اضغط للتأكيد" if confirm else "حذف",
                                           "⚠️" if confirm else "🗑️", discord.ButtonStyle.danger, row=3))
                self.add_item(ActionButton("back", "رجوع", "↩️", row=3))
                if confirm:
                    e.add_field(name="⚠️ تأكيد الحذف", value="سيُخفى المنتج نهائيًا من المتجر واللوحة، وتبقى "
                                                             "المشتريات السابقة والاستردادات محفوظة. اضغط «حذف» مرة ثانية للتأكيد.",
                                inline=False)
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

        per = 15
        pages = max(1, (len(items) + per - 1) // per)
        self.page = max(0, min(self.page, pages - 1))
        shown = items[self.page * per:(self.page + 1) * per]
        lines = []
        for i in shown:
            flag = "" if i["active"] else "  🙈"
            stock = "" if i["stock"] < 0 else f"  ·  📦 {i['stock']}"
            img = "  🖼️" if i["image_url"] else ""
            lines.append(f"`#{i['id']}` **{i['name']}** — `{i['price']:,}` {s['currency_emoji']}{stock}{img}{flag}")
        e.add_field(name=f"📦 المنتجات ({len(items)})" + (f"  ·  صفحة {self.page + 1}/{pages}" if pages > 1 else ""),
                    value="\n".join(lines) or "*لا توجد منتجات بعد — ابدأ بزر الإضافة*", inline=False)
        e.add_field(name=f"🟡 طلبات معلّقة ({len(pending)})",
                    value="\n".join(f"`#{p['id']}` <@{p['user_id']}> — {p['item_name']}" for p in pending[:8])
                    or "*لا توجد طلبات*", inline=False)
        if shown:
            self.add_item(ItemSelect(shown, s))
        if pending:
            self.add_item(OrderSelect(pending[:25]))
        self.add_item(ActionButton("new_custom", "إضافة مكافأة", "🎁", discord.ButtonStyle.success, row=3))
        self.add_item(ActionButton("new_role", "إضافة رتبة", "🎖️", discord.ButtonStyle.success, row=3))
        if pages > 1:
            self.add_item(ActionButton("prev_page", "السابق", "◀️", row=4, disabled=self.page == 0))
            self.add_item(ActionButton("next_page", "التالي", "▶️", row=4, disabled=self.page >= pages - 1))
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
        note = f"\n> {help_for(st)}" if help_for(st) else ""
        await self.refresh(interaction, (True, f"**{st.label}** {ui.ARROW} {format_value(st, value)}{note}"))

    async def on_action(self, interaction: discord.Interaction, action: str):
        db = self.bot.db
        if action == "back":
            self.mode, self.target, self.confirm_refund, self.confirm_delete = None, None, None, None
            return await self.refresh(interaction)
        if action in ("prev_page", "next_page"):
            self.page += -1 if action == "prev_page" else 1
            return await self.refresh(interaction)
        if action == "explain":
            sec = self.section
            if sec is None:
                return await self.refresh(interaction)
            if not await self.guard(interaction, sec.perm):
                return
            values = await db.get_settings(self.guild_id)
            pages = settings_help_pages(sec, values)
            return await ui.Paginator(pages, interaction.user.id).start(interaction, ephemeral=True)
        if action == "music_perms":
            if not await self.guard(interaction, ADMIN):
                return
            self.section, self.mode, self.target = SECTIONS_BY_KEY["permissions"], "perm_roles", "music"
            return await self.refresh(interaction)
        if action == "home":
            self.section, self.mode, self.target, self.page = None, None, None, 0
            self.confirm_refund = self.confirm_delete = None
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
            if not item or item["deleted"]:
                return await self.refresh(interaction, (False, "المنتج غير موجود."))
            return await interaction.response.send_modal(ItemModal(self, kind=item["kind"], item=item))
        if action == "stock_item":
            item = await db.shop_item(self.guild_id, self.target)
            if not item or item["deleted"]:
                return await self.refresh(interaction, (False, "المنتج غير موجود."))
            return await interaction.response.send_modal(StockModal(self, item))
        if action == "delete_item":
            item = await db.shop_item(self.guild_id, self.target)
            if not item or item["deleted"]:
                self.mode = None
                return await self.refresh(interaction, (False, "المنتج غير موجود أو حُذف بالفعل."))
            if self.confirm_delete != item["id"]:        # ضغطة تأكيد ثانية قبل الحذف
                self.confirm_delete = item["id"]
                return await self.refresh(interaction)
            self.confirm_delete = None
            await db.delete_shop_item(self.guild_id, item["id"])
            await self.bot.send_log(interaction.guild, "economy", ui.info(
                "🗑️ حذف منتج", f"{interaction.user.mention} حذف `#{item['id']}` **{item['name']}**", ui.Colors.MOD))
            self.mode, self.target = None, None
            return await self.refresh(interaction, (True, f"حُذف **{item['name']}** من المتجر. "
                                                          "المشتريات السابقة محفوظة ولم تُحذف بيانات أحد."))
        if action == "toggle_item":
            item = await db.shop_item(self.guild_id, self.target)
            if not item or item["deleted"]:
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
        if sec.key == "commands":  # لوحة مستقلة بواجهتها (نفس الصلاحية والقفل)، وفيها زر للعودة هنا
            from cogs.cmd_admin import CommandsView
            cv = CommandsView(view.bot, interaction.user, interaction.guild)
            view.stop()
            return await interaction.response.edit_message(embed=await cv.build(), view=cv)
        view.section = sec
        await view.refresh(interaction)


class SettingSelect(discord.ui.Select):
    def __init__(self, settings: list[Setting], values: dict):
        options = [discord.SelectOption(label=st.label[:100], value=st.key, emoji=KIND_EMOJI[st.kind],
                                        description=f"{plain_value(st, values[st.key])} — {help_for(st)}"[:100])
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
        if st.kind == "role":
            view.mode, view.target = "role_setting", st.key
            return await view.refresh(interaction)
        await interaction.response.send_modal(SettingModal(view, st, current))


class ChannelPicker(discord.ui.ChannelSelect):
    def __init__(self, st: Setting):
        voice = st.key == "voice_home_channel"
        types = ([discord.ChannelType.voice, discord.ChannelType.stage_voice] if voice
                 else [discord.ChannelType.text, discord.ChannelType.news])
        super().__init__(placeholder=f"#️⃣ {st.label}"[:100], row=1, channel_types=types)
        self.st = st

    async def callback(self, interaction: discord.Interaction):
        view: PanelView = self.view
        if not await view.guard(interaction, SETTING_PERM):
            return
        channel = interaction.guild.get_channel(self.values[0].id)
        if channel is None:
            return await view.refresh(interaction, (False, "الروم غير موجود في هذا السيرفر."))
        perms = channel.permissions_for(interaction.guild.me)
        if self.st.key == "voice_home_channel":
            if not (perms.view_channel and perms.connect and perms.speak):
                return await view.refresh(interaction, (False, f"لا أستطيع الاتصال والتحدث في {channel.mention}. "
                                                               "امنحني صلاحيات العرض والاتصال والتحدث."))
        elif not (perms.view_channel and perms.send_messages and perms.embed_links):
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
        if view.mode == "role_setting":
            st = SETTINGS_BY_KEY.get(view.target)
            if st is None or st.kind != "role" or not await view.guard(interaction, SETTING_PERM):
                return
            role = roles[0] if roles else None
            if role is None or role.is_default():
                return await view.refresh(interaction, (False, "اختر رتبة صالحة (ليست @everyone)."))
            return await view.save_setting(interaction, st, role.id)
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
        view.mode, view.target, view.confirm_delete = "item", int(self.values[0]), None
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
        self.value = discord.ui.TextInput(label=f"القيمة ({hint})"[:45], default=str(current), required=False,
                                          max_length=200, placeholder=(help_for(st) or "اتركه فارغًا للافتراضي")[:100])
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
        self.price = discord.ui.TextInput(label="السعر بالنقاط", max_length=12, placeholder="مثال: 2000",
                                          default=str(item["price"]) if item else None)
        self.desc = discord.ui.TextInput(label="الوصف (اختياري)", required=False, max_length=300,
                                         style=discord.TextStyle.paragraph,
                                         default=(item["description"] or "") if item else None)
        self.image = discord.ui.TextInput(label="رابط صورة المنتج (اختياري، https)", required=False, max_length=400,
                                          placeholder="https://.../image.png",
                                          default=(item["image_url"] or "") if item else None)
        for x in (self.name, self.price, self.desc, self.image):
            self.add_item(x)
        self.stock = self.hours = None
        if kind == "role":   # المخزون للرتب يُعدَّل من زر «المخزون»
            self.hours = discord.ui.TextInput(label="مدة الرتبة بالساعات (0 = دائمة)", required=False, max_length=6,
                                              default=str(item["duration_hours"]) if item else "0")
            self.add_item(self.hours)
        else:
            self.stock = discord.ui.TextInput(label="الكمية (-1 = غير محدودة)", required=False, max_length=8,
                                              default=str(item["stock"]) if item else "-1")
            self.add_item(self.stock)

    async def apply(self, interaction):
        db, gid = self.panel.bot.db, self.panel.guild_id
        name = self.name.value.strip()
        if not name:
            raise ValueError("اسم المنتج مطلوب.")
        price = _int(self.price.value, "السعر", 0, 1_000_000_000)
        stock = _int(self.stock.value or "-1", "الكمية", -1, 1_000_000) if self.stock else None
        hours = _int(self.hours.value or "0", "المدة", 0, 87600) if self.hours else None
        image = images.validate_url(self.image.value)        # يرفع ValueError برسالة واضحة
        desc = self.desc.value.strip()
        if self.item:
            await db.update_shop_item(gid, self.item["id"], name=name, price=price, description=desc, stock=stock,
                                      duration_hours=hours, image_url=image)
            self.panel.mode = "item"
            await self.panel.bot.send_log(interaction.guild, "economy", ui.info(
                "✏️ تعديل منتج", f"{interaction.user.mention} عدّل `#{self.item['id']}` **{name}** (السعر `{price:,}`)",
                ui.Colors.LOG))
            return True, f"تم تعديل **{name}**."
        if self.role:  # إعادة الفحص عند الحفظ: ربما تغيّرت رتبة البوت أو صلاحيات الرتبة
            problem = role_sale_error(self.role, interaction.guild)
            if problem:
                raise ValueError(problem)
        item_id = await db.add_shop_item(gid, name, desc or None, price, self.kind,
                                         self.role.id if self.role else None, stock if stock is not None else -1,
                                         hours or 0, True, image_url=image or None)
        self.panel.mode, self.panel.target = "item", item_id
        await self.panel.bot.send_log(interaction.guild, "economy", ui.info(
            "🛒 منتج جديد", f"{interaction.user.mention} أضاف `#{item_id}` **{name}** بسعر `{price:,}`", ui.Colors.LOG))
        return True, f"أُضيف المنتج `#{item_id}` **{name}** إلى المتجر."


class StockModal(PanelModal):
    perm = "shop"

    def __init__(self, view: PanelView, item):
        super().__init__(view, f"مخزون {item['name']}"[:45])
        self.item = item
        self.stock = discord.ui.TextInput(label="الكمية المتاحة (-1 = غير محدودة)", max_length=8,
                                          default=str(item["stock"]))
        self.add_item(self.stock)

    async def apply(self, interaction):
        n = _int(self.stock.value, "الكمية", -1, 1_000_000)
        db, gid = self.panel.bot.db, self.panel.guild_id
        if not await db.shop_item(gid, self.item["id"]):
            raise ValueError("المنتج غير موجود.")
        await db.update_shop_item(gid, self.item["id"], stock=n)
        self.panel.mode = "item"
        await self.panel.bot.send_log(interaction.guild, "economy", ui.info(
            "📦 تعديل مخزون", f"{interaction.user.mention} ضبط مخزون `#{self.item['id']}` **{self.item['name']}** "
                              f"إلى {'غير محدود' if n < 0 else n}", ui.Colors.LOG))
        return True, f"المخزون الآن: **{'غير محدود' if n < 0 else f'{n:,}'}**."


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

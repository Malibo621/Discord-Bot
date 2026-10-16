"""لوحة الأوامر الإدارية /commands: استعراض الأوامر، تفعيلها/تعطيلها، تعديل أوصافها، وإدارة الاختصارات النصية.

الأمان: اللوحة للمسؤولين فقط؛ كل ضغطة/اختيار/نافذة تعيد فحص الصلاحية من الخادم، وتُقفل على صاحب اللوحة وسيرفرها،
وكل كتابة تستخدم interaction.guild_id (لا معرّفات قادمة من العميل). التغييرات تُسجَّل في روم السجل.

وضوح الأثر (يظهر للمسؤول داخل اللوحة أيضًا):
- تفعيل/تعطيل: فوري ويُحفظ، والأمر يبقى ظاهرًا في ديسكورد لكنه يرد «معطّل».
- وصف الأمر/الخيار: يُحفظ فورًا، ولا يظهر في قائمة ديسكورد للأعضاء إلا بعد «تطبيق على ديسكورد».
- الاختصارات: محلية فورية، وتُنفّذ الأمر الأصلي بنفس صلاحياته.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import registry, shortcuts, ui
from bot.checks import admin_only, is_admin
from bot.registry import CommandInfo

PAGE = 8
KINDS = [("all", "كل الأوامر", "📚"), ("general", "عامة", "🌐"), ("games", "ألعاب", "🎮"), ("admin", "إدارية", "🛡️")]
SUGGESTED = {
    "ban": "ban", "kick": "kick", "mute": "mute", "unmute": "unmute", "warn": "warn", "warns": "warnings",
    "clear": "clear", "bal": "balance", "lb": "leaderboard", "shop": "shop",
    "roulette": "game roulette", "math": "game math", "quiz": "game quiz", "typing": "game typing",
    "guess": "game guess", "scramble": "game scramble",
}


class CommandsView(ui.BaseView):
    deny_message = "هذه اللوحة خاصة بمن فتحها. استخدم /commands بنفسك."

    def __init__(self, bot, owner: discord.Member, guild: discord.Guild):
        super().__init__(owner_id=owner.id, timeout=600)
        self.bot = bot
        self.guild_id = guild.id
        self.mode = "list"            # list | detail | preview | pick_option | shortcuts | pick_command
        self.kind = "all"
        self.query = ""
        self.page = 0
        self.current: Optional[str] = None      # الأمر المفتوح (الاسم الكامل)
        self.sel_shortcut: Optional[str] = None
        self.pending: Optional[tuple] = None     # (نوع، أمر، خيار|None، نص جديد) بانتظار تأكيد المعاينة
        self.confirm: Optional[str] = None       # عملية خطرة بانتظار ضغطة ثانية
        self.status: Optional[tuple[bool, str]] = None
        self.last_interaction: Optional[discord.Interaction] = None

    # ------------------------------------------------------------------ الأمان
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(embed=ui.error("هذه اللوحة تخص سيرفرًا آخر."), ephemeral=True)
            return False
        return await super().interaction_check(interaction)

    async def guard(self, interaction: discord.Interaction) -> bool:
        """فحص من الخادم عند كل عملية (ويُستدعى أيضًا من النوافذ المنبثقة)."""
        if interaction.guild_id != self.guild_id or interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=ui.error("غير مسموح."), ephemeral=True)
            return False
        if not is_admin(interaction.user):
            self.mode = "list"
            await self.refresh(interaction, (False, "هذه اللوحة للمسؤولين فقط، ولم تعد تملك الصلاحية."))
            return False
        return True

    # ------------------------------------------------------------------ العرض
    async def refresh(self, interaction: discord.Interaction, status: Optional[tuple[bool, str]] = None):
        if status is not None:
            self.status = status
        embed = await self.build()
        self.last_interaction = interaction
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if self.last_interaction:
            try:
                await self.last_interaction.edit_original_response(
                    embed=ui.info("🧩  لوحة الأوامر", "انتهت مدة الجلسة. افتحها من جديد بـ `/commands`.", ui.Colors.LOG),
                    view=None)
            except discord.HTTPException:
                pass

    def _filtered(self) -> list:
        q = self.query.strip().lower()
        out = []
        for c in registry.leaves(self.bot.tree):
            if self.kind != "all" and registry.kind_of(c) != self.kind:
                continue
            if q and q not in c.qualified_name.lower() and q not in str(c.description).lower():
                continue
            out.append(c)
        return out

    def _command(self) -> Optional[app_commands.Command]:
        return registry.find(self.bot.tree, self.current) if self.current else None

    async def build(self) -> discord.Embed:
        self.clear_items()
        settings = await self.bot.db.get_settings(self.guild_id)
        if self.mode in ("detail", "preview", "pick_option") and self._command() is None:
            self.mode = "list"
        if self.mode == "list":
            e = await self._list_embed()
        elif self.mode in ("pick_command",):
            e = await self._pick_command_embed()
        elif self.mode == "shortcuts":
            e = await self._shortcuts_embed(settings)
        else:
            e = await self._detail_embed()
        if self.status:
            ok, text = self.status
            e.add_field(name="✅ تمت العملية" if ok else "⛔ لم تتم العملية", value=text[:1024], inline=False)
            e.colour = ui.Colors.SUCCESS if ok else ui.Colors.ERROR
            self.status = None
        guild = self.bot.get_guild(self.guild_id)
        return ui.brand(e, guild, "لوحة الأوامر")

    # ---- قائمة الأوامر
    async def _list_embed(self) -> discord.Embed:
        items = self._filtered()
        pages = max(1, (len(items) + PAGE - 1) // PAGE)
        self.page = max(0, min(self.page, pages - 1))
        chunk = items[self.page * PAGE:(self.page + 1) * PAGE]
        infos = [await registry.info_for(self.bot, self.guild_id, c) for c in chunk]
        lines = []
        for i in infos:
            state = "🟢" if i.enabled else "🔴"
            sc = f"  ·  🔗 {' '.join('`' + s + '`' for s in i.shortcuts)}" if i.shortcuts else ""
            lines.append(f"{state} **/{i.qualified}** — {i.description[:70]}\n└ {i.kind_label}  ·  🔐 {i.perm_label}{sc}")
        e = ui.info("🧩  الأوامر المسجّلة", "\n".join(lines) or "*لا توجد أوامر مطابقة*", ui.Colors.ADMIN)
        filt = dict((k, l) for k, l, _ in KINDS)[self.kind]
        e.set_footer(text=f"{len(items)} أمر  ·  التصنيف: {filt}" + (f"  ·  بحث: {self.query}" if self.query else "")
                     + f"  ·  صفحة {self.page + 1}/{pages}")
        self._add_dirty_note(e)

        self.add_item(KindSelect(self.kind))
        if chunk:
            self.add_item(CommandSelect(infos, "📂 اختر أمرًا لإدارته..."))
        self.add_item(Btn("prev", "السابق", "◀️", row=2, disabled=self.page == 0))
        self.add_item(Btn("next", "التالي", "▶️", row=2, disabled=self.page >= pages - 1))
        self.add_item(Btn("search", "بحث", "🔍", row=2))
        if self.query or self.kind != "all":
            self.add_item(Btn("clear_filter", "مسح التصفية", "🧹", row=2))
        self.add_item(Btn("shortcuts", "الاختصارات", "🔗", discord.ButtonStyle.primary, row=3))
        self.add_item(Btn("sync", "تطبيق على ديسكورد", "🔄", row=3))
        self.add_item(Btn("home", "لوحة التحكم", "🏠", row=3))
        return e

    def _add_dirty_note(self, e: discord.Embed):
        if getattr(self.bot, "desc_dirty", False):
            e.add_field(name="⚠️ أوصاف لم تُطبَّق بعد", value="غيّرت أوصافًا محفوظة؛ اضغط **تطبيق على ديسكورد** "
                                                          "لتظهر لجميع الأعضاء في قائمة الأوامر.", inline=False)

    # ---- تفاصيل أمر
    async def _detail_embed(self) -> discord.Embed:
        cmd = self._command()
        i = await registry.info_for(self.bot, self.guild_id, cmd)
        cfg = (await self.bot.db.command_configs(self.guild_id)).get(i.qualified, {})
        e = ui.info(f"{'🟢' if i.enabled else '🔴'}  /{i.qualified}", i.description, ui.Colors.ADMIN)
        if i.description != i.default_description:
            e.add_field(name="الوصف الأصلي", value=i.default_description, inline=False)
        e.add_field(name="التصنيف", value=i.kind_label)
        e.add_field(name="الصلاحية المطلوبة", value=i.perm_label)
        e.add_field(name="الحالة", value="🟢 مفعّل" if i.enabled else "🔴 معطّل" + ("" if not i.protected else ""))
        if i.options:
            rows = []
            for o in i.options:
                mark = "مطلوب" if o.required else "اختياري"
                ch = f" — الخيارات: {'، '.join(o.choices)}" if o.choices else ""
                edited = " ✏️" if o.name in cfg.get("options", {}) else ""
                rows.append(f"`{o.name}` ({o.type_label}، {mark}){edited}\n└ {o.description}{ch}")
            e.add_field(name="الخيارات", value="\n".join(rows)[:1024], inline=False)
        if i.shortcuts:
            prefix = (await self.bot.db.get_settings(self.guild_id))["shortcut_prefix"]
            e.add_field(name="الاختصارات النصية", value=" ".join(f"`{prefix}{s}`" for s in i.shortcuts), inline=False)
        elif i.shortcut_ok:
            e.add_field(name="الاختصارات النصية", value="لا يوجد — يمكنك إضافة اختصار بزر ➕", inline=False)
        else:
            e.add_field(name="الاختصارات النصية", value=f"غير متاحة: {i.shortcut_reason}", inline=False)
        if i.protected:
            e.add_field(name="🔒 محمي", value="لا يمكن تعطيل هذا الأمر حتى لا تفقد الوصول للوحة الإدارة.", inline=False)
        self._add_dirty_note(e)

        if self.mode == "preview" and self.pending:
            kind, _qn, opt, new = self.pending
            old = (cfg.get("options", {}).get(opt) or registry.default_description(i.qualified, opt)) if opt else i.description
            e.add_field(name="👁️ معاينة التعديل قبل الحفظ",
                        value=f"**{'وصف الخيار `' + opt + '`' if opt else 'وصف الأمر'}**\n"
                              f"قبل: {old}\nبعد: **{new}**\n\n"
                              "سيُحفظ فورًا، ويظهر للأعضاء بعد «تطبيق على ديسكورد».", inline=False)
            self.add_item(Btn("save_pending", "حفظ", "✅", discord.ButtonStyle.success, row=0))
            self.add_item(Btn("cancel_pending", "إلغاء", "❌", discord.ButtonStyle.secondary, row=0))
            return e
        if self.mode == "pick_option":
            e.add_field(name="🧩 اختر الخيار", value="اختر الخيار الذي تريد تعديل وصفه.", inline=False)
            self.add_item(OptionSelect(i.options))
            self.add_item(Btn("back_detail", "رجوع", "↩️", row=1))
            return e

        if not i.protected:
            self.add_item(Btn("toggle", "تعطيل" if i.enabled else "تفعيل", "⛔" if i.enabled else "✅",
                              discord.ButtonStyle.danger if i.enabled else discord.ButtonStyle.success, row=0))
        self.add_item(Btn("edit_desc", "تعديل الوصف", "✏️", row=0))
        if i.options:
            self.add_item(Btn("edit_option", "وصف خيار", "🧩", row=0))
        changed = bool(cfg)
        self.add_item(Btn("reset", "اضغط للتأكيد" if self.confirm == "reset" else "استعادة الافتراضي",
                          "⚠️" if self.confirm == "reset" else "↩️",
                          discord.ButtonStyle.danger if self.confirm == "reset" else discord.ButtonStyle.secondary,
                          row=0, disabled=not changed))
        if i.shortcut_ok:
            self.add_item(Btn("add_shortcut", "إضافة اختصار", "➕", discord.ButtonStyle.success, row=1))
        if i.shortcuts:
            self.add_item(Btn("shortcuts_of", "اختصارات الأمر", "🔗", row=1))
        self.add_item(Btn("back_list", "رجوع للقائمة", "◀️", row=1))
        return e

    # ---- الاختصارات
    async def _shortcuts_embed(self, settings: dict) -> discord.Embed:
        table = await self.bot.db.shortcuts(self.guild_id)
        prefix = settings["shortcut_prefix"]
        items = sorted(table.items())
        if self.current:   # عرض اختصارات أمر محدد
            items = [(s, c) for s, c in items if c == self.current]
        lines = []
        for s, c in items[:25]:
            cmd = registry.find(self.bot.tree, c)
            usage_line = " ".join(f"<{p.display_name}>" if p.required else f"[{p.display_name}]"
                                  for p in cmd.parameters) if cmd else "⚠️ الأمر لم يعد موجودًا"
            mark = "👉 " if s == self.sel_shortcut else ""
            lines.append(f"{mark}`{prefix}{s}` ← **/{c}**\n└ `{prefix}{s} {usage_line}`".rstrip())
        state = "🟢 مفعّلة" if settings["shortcuts_enabled"] else "🔴 معطّلة"
        e = ui.info("🔗  الاختصارات النصية",
                    f"الحالة: {state}  ·  البادئة: `{prefix}`\n"
                    "اكتب الاختصار في أي روم مع البادئة، مثل "
                    f"`{prefix}ban @user السبب`، فيُنفَّذ الأمر الأصلي بنفس صلاحياته.\n"
                    "لا تعمل أوامر Slash بدون `/` في ديسكورد نفسه؛ هذه آلية نصية يعالجها البوت.\n\n"
                    + ("\n".join(lines) if lines else "*لا توجد اختصارات بعد — أضف واحدًا أو استخدم الاقتراحات.*"),
                    ui.Colors.ADMIN)
        if self.current:
            e.title = f"🔗  اختصارات /{self.current}"
        if items:
            self.add_item(ShortcutSelect([s for s, _ in items[:25]], table, prefix, self.sel_shortcut))
        self.add_item(Btn("new_shortcut", "إضافة", "➕", discord.ButtonStyle.success, row=1))
        if self.sel_shortcut and self.sel_shortcut in table:
            self.add_item(Btn("rename_shortcut", "إعادة تسمية", "✏️", row=1))
            self.add_item(Btn("delete_shortcut", "اضغط للتأكيد" if self.confirm == "delete" else "حذف",
                              "⚠️" if self.confirm == "delete" else "🗑️", discord.ButtonStyle.danger, row=1))
        self.add_item(Btn("suggest", "اقتراحات", "💡", row=2))
        self.add_item(Btn("toggle_shortcuts", "إيقاف" if settings["shortcuts_enabled"] else "تشغيل", "⏯️", row=2))
        self.add_item(Btn("prefix", "البادئة", "🔣", row=2))
        self.add_item(Btn("back_list", "رجوع للأوامر", "◀️", row=2))
        return e

    async def _pick_command_embed(self) -> discord.Embed:
        items = [c for c in self._filtered() if registry.shortcut_support(c)[0]]
        pages = max(1, (len(items) + 24) // 25)
        self.page = max(0, min(self.page, pages - 1))
        chunk = items[self.page * 25:(self.page + 1) * 25]
        e = ui.info("➕  اختر الأمر الذي سيُنشأ له اختصار",
                    "اختر أمرًا من القائمة ثم اكتب اسم الاختصار. استخدم 🔍 للبحث.", ui.Colors.ADMIN)
        e.set_footer(text=f"{len(items)} أمر قابل للاختصار" + (f"  ·  بحث: {self.query}" if self.query else ""))
        if chunk:
            self.add_item(PickCommandSelect(chunk))
        self.add_item(Btn("prev", "السابق", "◀️", row=1, disabled=self.page == 0))
        self.add_item(Btn("next", "التالي", "▶️", row=1, disabled=self.page >= pages - 1))
        self.add_item(Btn("search", "بحث", "🔍", row=1))
        self.add_item(Btn("back_shortcuts", "رجوع", "↩️", row=1))
        return e

    # ------------------------------------------------------------------ الإجراءات
    async def log(self, interaction: discord.Interaction, text: str):
        await self.bot.send_log(interaction.guild, "log", ui.info(
            "🧩 تعديل أوامر", f"{interaction.user.mention} {text}", ui.Colors.LOG))

    async def _apply_memory(self):
        registry.apply_overrides(self.bot.tree, await self.bot.db.command_configs(self.guild_id))

    async def _save_cfg(self, interaction, qualified: str, *, enabled=None, description="keep", option=None,
                        option_text=None, reset=False):
        db = self.bot.db
        cfg = dict((await db.command_configs(self.guild_id)).get(qualified, {"enabled": True, "description": None,
                                                                          "options": {}}))
        options = dict(cfg.get("options", {}))
        desc = cfg.get("description")
        en = cfg.get("enabled", True)
        if reset:
            en, desc, options = True, None, {}
        if enabled is not None:
            en = enabled
        if description != "keep":
            desc = description
        if option is not None:
            options[option] = option_text
        await db.save_command_config(self.guild_id, qualified, enabled=en, description=desc, options=options,
                                     admin_id=interaction.user.id)
        await self._apply_memory()

    async def on_action(self, interaction: discord.Interaction, action: str):
        db = self.bot.db
        if action != "reset" and action != "delete_shortcut":
            self.confirm = None
        if action in ("prev", "next"):
            self.page += -1 if action == "prev" else 1
            return await self.refresh(interaction)
        if action == "search":
            return await interaction.response.send_modal(SearchModal(self))
        if action == "clear_filter":
            self.query, self.kind, self.page = "", "all", 0
            return await self.refresh(interaction)
        if action == "home":
            from cogs.panel import PanelView  # استيراد متأخر لتجنب الدوران
            view = PanelView(self.bot, interaction.user, interaction.guild)
            self.stop()
            return await interaction.response.edit_message(embed=await view.build(interaction.user), view=view)
        if action == "sync":
            wait = registry.sync_wait()
            if wait > 0:
                return await self.refresh(interaction, (False, f"انتظر {wait:.0f} ثانية قبل المزامنة التالية "
                                                              "(حد ديسكورد لحماية البوت من الحظر)."))
            await interaction.response.defer()
            gid = os.getenv("GUILD_ID")
            try:
                n = await registry.sync_to_discord(self.bot, int(gid) if gid else None)
            except discord.HTTPException as exc:
                return await self.refresh(interaction, (False, f"رفض ديسكورد المزامنة: {exc.text or exc.status}"))
            self.bot.desc_dirty = False
            await self.log(interaction, f"طبّق الأوصاف على ديسكورد ({n} أمر)")
            note = "" if gid else "\nملاحظة: دون `GUILD_ID` تُزامَن الأوامر عالميًا وقد تتأخر حتى ساعة."
            return await self.refresh(interaction, (True, f"زُوِّد ديسكورد بـ {n} أمرًا بأوصافها الحالية.{note}"))
        if action == "shortcuts":
            self.mode, self.current, self.sel_shortcut = "shortcuts", None, None
            return await self.refresh(interaction)
        if action == "shortcuts_of":
            self.mode, self.sel_shortcut = "shortcuts", None
            return await self.refresh(interaction)
        if action == "back_list":
            self.mode, self.current, self.sel_shortcut = "list", None, None
            return await self.refresh(interaction)
        if action == "back_detail":
            self.mode = "detail"
            return await self.refresh(interaction)
        if action == "back_shortcuts":
            self.mode, self.page = "shortcuts", 0
            return await self.refresh(interaction)
        if action == "new_shortcut":
            self.mode, self.page = "pick_command", 0
            return await self.refresh(interaction)

        qn = self.current
        if action == "toggle":
            if qn.split()[0] in registry.PROTECTED:
                return await self.refresh(interaction, (False, "هذا الأمر محمي من التعطيل."))
            cfg = (await db.command_configs(self.guild_id)).get(qn, {})
            new_state = not cfg.get("enabled", True)
            await self._save_cfg(interaction, qn, enabled=new_state)
            await self.log(interaction, f"{'فعّل' if new_state else 'عطّل'} الأمر `/{qn}`")
            return await self.refresh(interaction, (True, f"`/{qn}` الآن {'مفعّل' if new_state else 'معطّل'}. "
                                                          "التغيير فوري ولا يحتاج مزامنة."))
        if action == "edit_desc":
            return await interaction.response.send_modal(DescModal(self, qn, None))
        if action == "edit_option":
            self.mode = "pick_option"
            return await self.refresh(interaction)
        if action == "save_pending" and self.pending:
            kind, qn2, opt, new = self.pending
            if opt:
                await self._save_cfg(interaction, qn2, option=opt, option_text=new)
            else:
                await self._save_cfg(interaction, qn2, description=new)
            self.bot.desc_dirty = True
            await self.log(interaction, f"عدّل وصف {'الخيار `' + opt + '` في ' if opt else ''}`/{qn2}`")
            self.pending, self.mode = None, "detail"
            return await self.refresh(interaction, (True, "حُفظ الوصف. اضغط «تطبيق على ديسكورد» لإظهاره للأعضاء."))
        if action == "cancel_pending":
            self.pending, self.mode = None, "detail"
            return await self.refresh(interaction, (True, "أُلغي التعديل ولم يُحفظ شيء."))
        if action == "reset":
            if self.confirm != "reset":
                self.confirm = "reset"
                return await self.refresh(interaction)
            self.confirm = None
            await self._save_cfg(interaction, qn, reset=True)
            self.bot.desc_dirty = True
            await self.log(interaction, f"أعاد إعدادات `/{qn}` للافتراضي")
            return await self.refresh(interaction, (True, "أُعيد الأمر لإعداداته الافتراضية (الوصف والخيارات والتفعيل). "
                                                          "الاختصارات لم تتغير."))
        if action == "add_shortcut":
            return await interaction.response.send_modal(ShortcutModal(self, qn))

        # ---- الاختصارات
        table = await db.shortcuts(self.guild_id)
        if action == "suggest":
            added, skipped = 0, 0
            for name, target in SUGGESTED.items():
                cmd = registry.find(self.bot.tree, target)
                if cmd is None:
                    continue
                try:
                    name = shortcuts.validate_shortcut(self.bot.tree, table, name, cmd)
                except ValueError:
                    skipped += 1
                    continue
                if await db.add_shortcut(self.guild_id, name, target, interaction.user.id):
                    table = await db.shortcuts(self.guild_id)
                    added += 1
            if added:
                await self.log(interaction, f"أضاف {added} اختصارات مقترحة")
            return await self.refresh(interaction, (True, f"أُضيف {added} اختصارًا، وتُخطّي {skipped} (موجود أو متعارض)."))
        if action == "toggle_shortcuts":
            current = (await db.get_settings(self.guild_id))["shortcuts_enabled"]
            await db.set_setting(self.guild_id, "shortcuts_enabled", not current)
            await self.log(interaction, f"{'أوقف' if current else 'شغّل'} الاختصارات النصية")
            return await self.refresh(interaction, (True, f"الاختصارات النصية الآن {'معطّلة' if current else 'مفعّلة'}."))
        if action == "prefix":
            current = (await db.get_settings(self.guild_id))["shortcut_prefix"]
            return await interaction.response.send_modal(PrefixModal(self, current))
        if action == "rename_shortcut" and self.sel_shortcut in table:
            return await interaction.response.send_modal(ShortcutModal(self, table[self.sel_shortcut],
                                                                       rename=self.sel_shortcut))
        if action == "delete_shortcut" and self.sel_shortcut in table:
            if self.confirm != "delete":
                self.confirm = "delete"
                return await self.refresh(interaction)
            self.confirm = None
            name = self.sel_shortcut
            await db.delete_shortcut(self.guild_id, name)
            self.sel_shortcut = None
            await self.log(interaction, f"حذف الاختصار `{name}`")
            return await self.refresh(interaction, (True, f"حُذف الاختصار `{name}`. الأمر الأصلي لم يتأثر."))
        await self.refresh(interaction)


# ====================================================================== المكوّنات
class Btn(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary, *, row: int,
                 disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if await view.guard(interaction):
            await view.on_action(interaction, self.action)


class KindSelect(discord.ui.Select):
    def __init__(self, current: str):
        super().__init__(placeholder="🗂️ تصنيف الأوامر...", row=0, options=[
            discord.SelectOption(label=label, value=key, emoji=emoji, default=key == current)
            for key, label, emoji in KINDS])

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if await view.guard(interaction) and self.values[0] in dict((k, 0) for k, _, _ in KINDS):
            view.kind, view.page = self.values[0], 0
            await view.refresh(interaction)


class CommandSelect(discord.ui.Select):
    def __init__(self, infos: list[CommandInfo], placeholder: str):
        super().__init__(placeholder=placeholder, row=1, options=[
            discord.SelectOption(label=f"/{i.qualified}"[:100], value=i.qualified, emoji="🟢" if i.enabled else "🔴",
                                 description=i.description[:100]) for i in infos])

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if not await view.guard(interaction):
            return
        if registry.find(view.bot.tree, self.values[0]) is None:
            return await view.refresh(interaction, (False, "الأمر غير موجود."))
        view.current, view.mode, view.confirm = self.values[0], "detail", None
        await view.refresh(interaction)


class PickCommandSelect(discord.ui.Select):
    def __init__(self, cmds):
        super().__init__(placeholder="➕ اختر أمرًا...", row=0, options=[
            discord.SelectOption(label=f"/{c.qualified_name}"[:100], value=c.qualified_name,
                                 description=str(c.description)[:100]) for c in cmds])

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if not await view.guard(interaction):
            return
        cmd = registry.find(view.bot.tree, self.values[0])
        if cmd is None or not registry.shortcut_support(cmd)[0]:
            return await view.refresh(interaction, (False, "لا يمكن إنشاء اختصار لهذا الأمر."))
        await interaction.response.send_modal(ShortcutModal(view, cmd.qualified_name))


class OptionSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="🧩 اختر خيارًا...", row=0, options=[
            discord.SelectOption(label=o.name[:100], value=o.name, description=o.description[:100]) for o in options])

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if not await view.guard(interaction):
            return
        cmd = view._command()
        if cmd is None or self.values[0] not in cmd._params:
            return await view.refresh(interaction, (False, "الخيار غير موجود."))
        await interaction.response.send_modal(DescModal(view, cmd.qualified_name, self.values[0]))


class ShortcutSelect(discord.ui.Select):
    def __init__(self, names: list[str], table: dict[str, str], prefix: str, selected: Optional[str]):
        super().__init__(placeholder="🔗 اختر اختصارًا...", row=0, options=[
            discord.SelectOption(label=f"{prefix}{n}"[:100], value=n, description=f"/{table[n]}"[:100],
                                 default=n == selected) for n in names])

    async def callback(self, interaction: discord.Interaction):
        view: CommandsView = self.view
        if await view.guard(interaction):
            view.sel_shortcut, view.confirm = self.values[0], None
            await view.refresh(interaction)


# ====================================================================== النوافذ
class _Modal(discord.ui.Modal):
    def __init__(self, view: CommandsView, title: str):
        super().__init__(title=title[:45], timeout=300)
        self.panel = view

    async def on_submit(self, interaction: discord.Interaction):
        if not await self.panel.guard(interaction):
            return
        try:
            await self.apply(interaction)
        except ValueError as exc:
            await self.panel.refresh(interaction, (False, str(exc)))

    async def apply(self, interaction: discord.Interaction):
        raise NotImplementedError

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        ui.log.exception("Commands panel modal error", exc_info=error)
        await ui.reply(interaction, ui.error("حدث خطأ غير متوقع أثناء الحفظ."), ephemeral=True)


class SearchModal(_Modal):
    def __init__(self, view: CommandsView):
        super().__init__(view, "بحث في الأوامر")
        self.q = discord.ui.TextInput(label="كلمة البحث (اسم الأمر أو وصفه)", required=False, max_length=50,
                                      default=view.query or None)
        self.add_item(self.q)

    async def apply(self, interaction):
        self.panel.query, self.panel.page = self.q.value.strip(), 0
        await self.panel.refresh(interaction)


class DescModal(_Modal):
    def __init__(self, view: CommandsView, qualified: str, option: Optional[str]):
        super().__init__(view, f"وصف {option}" if option else f"وصف /{qualified}"[:45])
        self.qualified, self.option = qualified, option
        cmd = registry.find(view.bot.tree, qualified)
        current = str(cmd._params[option].description) if option else str(cmd.description)
        self.text = discord.ui.TextInput(label="الوصف الجديد (حتى 100 حرف)", default=current[:100], max_length=100,
                                         style=discord.TextStyle.paragraph)
        self.add_item(self.text)

    async def apply(self, interaction):
        new = registry.validate_description(self.text.value)
        self.panel.pending = ("desc", self.qualified, self.option, new)
        self.panel.mode = "preview"
        await self.panel.refresh(interaction)


class ShortcutModal(_Modal):
    def __init__(self, view: CommandsView, target: str, rename: Optional[str] = None):
        super().__init__(view, "إعادة تسمية الاختصار" if rename else f"اختصار لـ /{target}"[:45])
        self.target, self.rename = target, rename
        self.name = discord.ui.TextInput(label="اسم الاختصار (بلا بادئة)", max_length=20, default=rename,
                                         placeholder="مثال: ban")
        self.add_item(self.name)

    async def apply(self, interaction):
        db, gid = self.panel.bot.db, self.panel.guild_id
        cmd = registry.find(self.panel.bot.tree, self.target)
        if cmd is None:
            raise ValueError("الأمر لم يعد موجودًا.")
        table = await db.shortcuts(gid)
        name = shortcuts.validate_shortcut(self.panel.bot.tree, table, self.name.value, cmd, replacing=self.rename)
        if self.rename:
            if name == self.rename:
                raise ValueError("الاسم الجديد مطابق للقديم.")
            if not await db.rename_shortcut(gid, self.rename, name):
                raise ValueError("تعذّرت إعادة التسمية (ربما حُذف الاختصار أو الاسم مستخدم).")
            self.panel.sel_shortcut = name
            await self.panel.log(interaction, f"أعاد تسمية الاختصار `{self.rename}` إلى `{name}`")
            msg = f"صار الاختصار `{name}` ← `/{self.target}`."
        else:
            if not await db.add_shortcut(gid, name, self.target, interaction.user.id):
                raise ValueError(f"الاختصار `{name}` موجود مسبقًا.")
            self.panel.sel_shortcut = name
            await self.panel.log(interaction, f"أضاف الاختصار `{name}` ← `/{self.target}`")
            prefix = (await db.get_settings(gid))["shortcut_prefix"]
            msg = f"أُضيف `{prefix}{name}` ← `/{self.target}` (فوري، بنفس صلاحيات الأمر الأصلي)."
        self.panel.mode = "shortcuts"
        await self.panel.refresh(interaction, (True, msg))


class PrefixModal(_Modal):
    def __init__(self, view: CommandsView, current: str):
        super().__init__(view, "بادئة الاختصارات")
        self.prefix = discord.ui.TextInput(label="البادئة (1-3 رموز)", default=current, max_length=3,
                                           placeholder="مثال: ! أو .")
        self.add_item(self.prefix)

    async def apply(self, interaction):
        from bot.settings_schema import SETTINGS_BY_KEY, parse_value
        value = parse_value(SETTINGS_BY_KEY["shortcut_prefix"], self.prefix.value)
        await self.panel.bot.db.set_setting(self.panel.guild_id, "shortcut_prefix", value)
        await self.panel.log(interaction, f"غيّر بادئة الاختصارات إلى `{value}`")
        await self.panel.refresh(interaction, (True, f"البادئة الآن `{value}`."))


# ====================================================================== الأمر والمستمع
class CmdAdmin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_use: dict[tuple[int, int], float] = {}
        bot.desc_dirty = False

    @app_commands.command(name="commands", description="لوحة الأوامر: استعراضها وتعطيلها وتعديل أوصافها واختصاراتها")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @admin_only()
    async def commands_panel(self, interaction: discord.Interaction):
        view = CommandsView(self.bot, interaction.user, interaction.guild)
        await ui.reply(interaction, await view.build(), view=view, ephemeral=True)
        view.last_interaction = interaction

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        found = await shortcuts.find_target(self.bot, message)
        if not found:
            return
        name, cmd, rest, prefix = found
        key = (message.guild.id, message.author.id)
        now = time.monotonic()
        if now - self._last_use.get(key, 0) < 1.5:   # منع الإغراق
            return
        self._last_use[key] = now
        await shortcuts.run(self.bot, message, name, cmd, rest, prefix)


async def setup(bot):
    await bot.add_cog(CmdAdmin(bot))

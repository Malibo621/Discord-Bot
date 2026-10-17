"""المتجر: عرض المنتجات، الشراء بالنقاط، المقتنيات، وإدارة المنتجات والطلبات."""
from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot import cards, images, ui
from bot.checks import has_category, require
from bot.db import InsufficientFunds, LimitReached
from bot.utils import ts

log = logging.getLogger("bot.shop")
STORE_PAGE = 6

STATUS = {
    "delivered": "🟢 مُسلَّم",
    "pending": "🟡 بانتظار التسليم",
    "expired": "⚫ منتهي",
    "refunded": "↩️ مُسترجع",
    "processing": "🔄 قيد التسليم",
    "failed": "⚠️ فشل التسليم (لم يُخصم)",
}
MAX_RETRIES = 5


def item_line(item, s) -> str:
    kind = f"🎖️ رتبة <@&{item['role_id']}>" if item["kind"] == "role" else "🎁 مكافأة خاصة"
    extras = []
    if item["duration_hours"]:
        h = item["duration_hours"]
        extras.append(f"⏳ {h // 24} يوم" if h % 24 == 0 else f"⏳ {h} ساعة")
    if item["stock"] >= 0:
        extras.append(f"📦 متبقي {item['stock']}")
    extra = (" • " + " • ".join(extras)) if extras else ""
    desc = f"\n> {item['description']}" if item["description"] else ""
    return f"**`#{item['id']}` {item['name']}** — {ui.money(item['price'], s)}\n{kind}{extra}{desc}"


def role_sale_error(role: discord.Role, guild: discord.Guild) -> Optional[str]:
    """يرجع سبب منع بيع الرتبة، أو None إن كانت آمنة للبيع."""
    if role.managed or role.is_default():
        return "لا يمكن بيع هذه الرتبة (رتبة بوت/تكامل أو @everyone)."
    if role >= guild.me.top_role:
        return "الرتبة أعلى من رتبة البوت. ارفع رتبة البوت فوقها."
    if role.permissions.administrator or role.permissions.manage_guild or role.permissions.ban_members:
        return "لأمان السيرفر، لا يمكن بيع رتبة تحمل صلاحيات إدارية."
    return None


async def deliver_purchase(bot, guild: discord.Guild, admin: discord.Member, purchase_id: int) -> tuple[bool, str]:
    db = bot.db
    p = await db.purchase_row(guild.id, purchase_id)
    if not p or not await db.mark_delivered(guild.id, purchase_id):
        return False, "لا يوجد طلب معلّق بهذا الرقم (ربما سُلّم أو استُرجع للتو)."
    member = guild.get_member(p["user_id"])
    if member:
        try:
            await member.send(embed=ui.success("تم تسليم طلبك",
                                               f"تم تسليم **{p['item_name']}** في **{guild.name}** 🎉"))
        except discord.HTTPException:
            pass
    await bot.send_log(guild, "economy", ui.info(
        "📦 تسليم طلب", f"{admin.mention} سلّم `#{purchase_id}` لـ <@{p['user_id']}>", ui.Colors.LOG))
    return True, f"طلب `#{purchase_id}` (**{p['item_name']}**) للعضو <@{p['user_id']}>"


async def refund_purchase(bot, guild: discord.Guild, admin: discord.Member, purchase_id: int) -> tuple[bool, str]:
    db = bot.db
    p = await db.refund(guild.id, purchase_id, admin.id)
    if not p:
        return False, "لا يوجد طلب قابل للاسترجاع بهذا الرقم."
    item = await db.shop_item(guild.id, p["item_id"])
    member = guild.get_member(p["user_id"])
    if item and item["kind"] == "role" and member:
        role = guild.get_role(item["role_id"])
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason=f"استرجاع الطلب #{purchase_id}")
            except discord.HTTPException:
                pass
    s = await db.get_settings(guild.id)
    await bot.send_log(guild, "economy", ui.info(
        "↩️ استرجاع طلب", f"{admin.mention} استرجع `#{purchase_id}` • `{p['price']:,}` "
                          f"إلى <@{p['user_id']}>", ui.Colors.MOD))
    return True, f"أُعيد {ui.money(p['price'], s)} إلى <@{p['user_id']}>"


@dataclass
class PurchaseOutcome:
    ok: bool
    code: str                 # ok | not_found | no_role | no_perm | hierarchy | has_role | insufficient | limit | busy | role_failed
    message: str
    purchase_id: Optional[int] = None
    before: int = 0
    after: int = 0
    row: Any = None
    role: Optional[discord.Role] = None
    retryable: bool = False


def _role_problem(guild: discord.Guild, member: discord.Member, role: Optional[discord.Role]) -> Optional[tuple[str, str]]:
    """فحوص منح الرتبة قبل أي خصم. يرجع (الرمز، الرسالة) أو None إن كان كل شيء سليمًا."""
    if role is None:
        return "no_role", "رتبة هذا المنتج حُذفت. تواصل مع الإدارة."
    if not guild.me.guild_permissions.manage_roles:
        return "no_perm", "البوت يفتقد صلاحية Manage Roles. أبلغ الإدارة."
    if role.managed or role.is_default() or role >= guild.me.top_role:
        return "hierarchy", "لا أستطيع منح هذه الرتبة لأنها أعلى من رتبة البوت (أو رتبة خاصة). أبلغ الإدارة."
    if role in member.roles:
        return "has_role", "تملك هذه الرتبة مسبقًا، فلا حاجة لشرائها."
    return None


async def _grant_and_verify(guild: discord.Guild, member: discord.Member, role: discord.Role, reason: str) -> Optional[str]:
    """يمنح الرتبة ثم يتحقق فعليًا من وجودها عند العضو. يرجع None عند النجاح أو سبب الفشل."""
    try:
        await member.add_roles(role, reason=reason)
    except discord.Forbidden:
        return "ديسكورد رفض منح الرتبة (صلاحيات البوت أو ترتيب رتبته)"
    except discord.HTTPException as exc:
        return f"خطأ من ديسكورد ({exc.status})"
    try:   # لا نعلن النجاح قبل التأكد أن الرتبة وصلت فعلًا
        fresh = await guild.fetch_member(member.id)
        if role not in fresh.roles:
            return "لم تظهر الرتبة عند العضو بعد المنح"
    except discord.HTTPException:
        pass   # تعذّر التحقق (شبكة): نعتمد نجاح طلب المنح نفسه
    return None


async def complete_purchase(bot, guild: discord.Guild, member: discord.Member, item_id: int) -> PurchaseOutcome:
    """الشراء الكامل بعد تأكيد العضو. الترتيب: فحوص ← خصم ذرّي ← منح الرتبة ← تأكيد أو عكس كامل ← إشعار الإدارة.

    فشل منح الرتبة بعد الخصم يعكس الخصم والمخزون تلقائيًا في معاملة واحدة (فلا يُخصم شيء)، ويُسجَّل للتتبع،
    ويبقى للعضو زر «إعادة المحاولة» دون أي دفع مكرر.
    """
    db = bot.db
    busy: set = bot.__dict__.setdefault("_buying", set())
    key = (guild.id, member.id, item_id)
    if key in busy:
        return PurchaseOutcome(False, "busy", "لديك عملية شراء لهذا المنتج قيد التنفيذ، انتظر لحظات.")
    busy.add(key)
    try:
        row = await db.shop_item(guild.id, item_id)       # السعر والرتبة دائمًا من قاعدة البيانات لا من التفاعل
        if not row or not row["active"] or row["deleted"]:
            return PurchaseOutcome(False, "not_found", "المنتج غير موجود أو لم يعد معروضًا.")
        role = None
        if row["kind"] == "role":
            role = guild.get_role(row["role_id"])
            problem = _role_problem(guild, member, role)
            if problem:
                return PurchaseOutcome(False, problem[0], problem[1], row=row, role=role)
        before = await db.balance(guild.id, member.id)
        try:
            purchase_id, after = await db.purchase(guild.id, member.id, item_id)
        except InsufficientFunds:
            return PurchaseOutcome(False, "insufficient", "رصيدك لا يكفي لإتمام الشراء.", before=before, row=row)
        except LimitReached as exc:
            return PurchaseOutcome(False, "limit", exc.message, before=before, row=row)

        if role is not None:
            failure = await _grant_and_verify(guild, member, role, f"شراء من المتجر #{purchase_id}")
            if failure:
                await db.settle_purchase(guild.id, purchase_id, ok=False, reason=failure)
                log.warning("Role grant failed for purchase #%s (%s): %s", purchase_id, row["name"], failure)
                await notify_purchase(bot, guild, member, row, purchase_id, before, before, role, failure=failure)
                return PurchaseOutcome(False, "role_failed", f"تعذّر منح الرتبة: {failure}.\nلم يُخصم أي مبلغ من رصيدك.",
                                       purchase_id, before, before, row, role, retryable=True)
            await db.settle_purchase(guild.id, purchase_id, ok=True)
        await notify_purchase(bot, guild, member, row, purchase_id, before, after, role)
        return PurchaseOutcome(True, "ok", "", purchase_id, before, after, row, role)
    finally:
        busy.discard(key)


async def notify_purchase(bot, guild: discord.Guild, member: discord.Member, row, purchase_id: int, before: int,
                          after: int, role: Optional[discord.Role], *, failure: Optional[str] = None) -> None:
    """إشعار إداري منسّق لكل عملية شراء (أو فشل تسليم) مع منشن رتبة الإدارة المضبوطة في الإعدادات."""
    s = await bot.db.get_settings(guild.id)
    channel = guild.get_channel(s["shop_notify_channel"]) if s["shop_notify_channel"] else None
    if channel is None and s["economy_log_channel"]:
        channel = guild.get_channel(s["economy_log_channel"])
    if channel is None and s["log_channel"]:
        channel = guild.get_channel(s["log_channel"])
    if not isinstance(channel, discord.abc.Messageable):
        return
    ping = guild.get_role(s["shop_notify_role"]) if s["shop_notify_role"] else None
    now_ts = int(time.time())
    if failure:
        e = ui.error(f"{member.mention} حاول شراء **{row['name']}** وتعذّر منح الرتبة.\n"
                     f"السبب: {failure}\nلم يُخصم شيء (عُكست العملية تلقائيًا).", "⚠️ فشل تسليم رتبة")
        status = f"🔴 فشل المنح — طلب `#{purchase_id}` معكوس"
    else:
        e = ui.info("🛒 عملية شراء جديدة", f"{member.mention} اشترى **{row['name']}**", ui.Colors.GOLD)
        status = (f"🟢 مُنحت الرتبة {role.mention}" if role is not None else "🟡 بانتظار التسليم يدويًا")
    price = row["price"]
    e.add_field(name="👤 العضو", value=f"{member.mention}\n`{member.id}`")
    e.add_field(name="📦 المنتج", value=f"`#{row['id']}` {row['name']}")
    e.add_field(name="🏷️ الحالة", value=status)
    e.add_field(name="💰 السعر", value=ui.money(price, s))
    e.add_field(name="⬅️ الرصيد قبل", value=ui.money(before, s))
    e.add_field(name="➖ المخصوم", value=ui.money(0 if failure else price, s))
    e.add_field(name="➡️ الرصيد بعد", value=ui.money(after, s))
    e.add_field(name="🕒 الوقت", value=f"<t:{now_ts}:F>\n<t:{now_ts}:R>")
    e.add_field(name="🧾 رقم الطلب", value=f"`#{purchase_id}`")
    e.set_thumbnail(url=member.display_avatar.url)
    kwargs: dict = {"embed": e}
    if ping is not None:   # منشن الرتبة المحددة فقط، لا أي شخص أو رتبة أخرى
        kwargs["content"] = ping.mention
        kwargs["allowed_mentions"] = discord.AllowedMentions(roles=[ping], users=False, everyone=False)
    else:
        kwargs["allowed_mentions"] = discord.AllowedMentions.none()
    try:
        await channel.send(**kwargs)
    except discord.HTTPException:
        log.warning("Could not send purchase notification to #%s", getattr(channel, "name", "?"))


async def recover_processing(bot) -> int:
    """يُكمل أو يعكس مشتريات الرتب العالقة «قيد التسليم» (مثلًا بعد انهيار بين الخصم والمنح)."""
    n = 0
    for p in await bot.db.processing_purchases(90):
        guild = bot.get_guild(p["guild_id"])
        item = await bot.db.shop_item(p["guild_id"], p["item_id"])
        member = guild.get_member(p["user_id"]) if guild else None
        role = guild.get_role(item["role_id"]) if (guild and item) else None
        if member is not None and role is not None and role in member.roles:
            ok, reason = True, ""
        elif member is not None and role is not None:
            failure = await _grant_and_verify(guild, member, role, f"استكمال شراء #{p['id']}")
            ok, reason = failure is None, failure or ""
        else:
            ok, reason = False, "العضو أو الرتبة غير متاحين"
        if await bot.db.settle_purchase(p["guild_id"], p["id"], ok=ok, reason=reason or "استعادة تلقائية"):
            await bot.db.note_attempt(p["guild_id"], p["user_id"], p["item_id"], p["id"],
                                      "recovered" if ok else "reversed", reason or "استُكمل")
            log.warning("Recovered stuck purchase #%s -> %s", p["id"], "delivered" if ok else f"reversed ({reason})")
            n += 1
    return n


class RetryView(ui.BaseView):
    """زر إعادة المحاولة بعد فشل منح الرتبة: لا دفع مكرر لأن الخصم عُكس تلقائيًا."""

    deny_message = "هذه المحاولة تخص صاحب الطلب."

    def __init__(self, bot, owner: discord.Member, guild: discord.Guild, item_id: int):
        super().__init__(owner_id=owner.id, timeout=300)
        self.bot, self.guild, self.owner, self.item_id, self.tries = bot, guild, owner, item_id, 0

    @discord.ui.button(label="إعادة المحاولة", emoji="🔁", style=discord.ButtonStyle.primary)
    async def retry(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.tries += 1
        member = self.guild.get_member(interaction.user.id) or interaction.user
        out = await complete_purchase(self.bot, self.guild, member, self.item_id)
        s = await self.bot.db.get_settings(self.guild.id)
        if out.ok:
            self.stop()
            return await interaction.response.edit_message(embed=_success_embed(out, s), view=None)
        if out.retryable and self.tries < MAX_RETRIES:
            return await interaction.response.edit_message(embed=_failure_embed(out, retry_left=MAX_RETRIES - self.tries),
                                                           view=self)
        self.stop()
        await interaction.response.edit_message(embed=_failure_embed(out), view=None)


def _success_embed(out: PurchaseOutcome, s: dict) -> discord.Embed:
    row = out.row
    delivery = (f"تم منحك رتبة {out.role.mention}" if out.role is not None else "طلبك وصل للإدارة وسيتم تسليمه قريبًا.")
    expires = None
    return ui.success("تم الشراء بنجاح", f"**{row['name']}**\n{delivery}\n\n**رقم الطلب:** `#{out.purchase_id}`\n"
                                         f"**رصيدك الآن:** {ui.money(out.after, s)}")


def _failure_embed(out: PurchaseOutcome, retry_left: Optional[int] = None) -> discord.Embed:
    e = ui.error(out.message, "تعذّر إتمام الشراء")
    if out.retryable and retry_left is not None:
        e.set_footer(text=f"المحاولات المتبقية: {retry_left}")
    return e


async def purchase_flow(bot, interaction: discord.Interaction, item_id: int) -> None:
    """واجهة الشراء (مشتركة بين /buy وزر الشراء): فحوص أولية ← تأكيد ← complete_purchase ← نتيجة صريحة."""
    db = bot.db
    s = await db.get_settings(interaction.guild_id)
    row = await db.shop_item(interaction.guild_id, item_id)
    if not row or not row["active"] or row["deleted"]:
        return await ui.reply(interaction, ui.error("المنتج غير موجود."), ephemeral=True)
    if row["kind"] == "role":
        problem = _role_problem(interaction.guild, interaction.user, interaction.guild.get_role(row["role_id"]))
        if problem:
            return await ui.reply(interaction, ui.error(problem[1]), ephemeral=True)
    bal = await db.balance(interaction.guild_id, interaction.user.id)
    if bal < row["price"]:
        return await ui.reply(interaction, ui.error(
            f"رصيدك {ui.money(bal, s)} لا يكفي. تحتاج {ui.money(row['price'] - bal, s)} إضافية."), ephemeral=True)

    e = ui.info(f"{ui.Icons.SHOP} تأكيد الشراء", item_line(row, s), ui.Colors.GOLD)
    e.add_field(name="رصيدك بعد الشراء", value=ui.money(bal - row["price"], s))
    if row["image_url"]:
        e.set_thumbnail(url=row["image_url"])
    view = ui.ConfirmView(interaction.user.id)
    await ui.reply(interaction, e, view=view, ephemeral=True)
    await view.wait()
    if not view.value:
        cancelled = ui.info("تم إلغاء الشراء", color=ui.Colors.LOG)
        if view.interaction:
            return await view.interaction.response.edit_message(embed=cancelled, view=None)
        return await interaction.edit_original_response(embed=cancelled, view=None)

    out = await complete_purchase(bot, interaction.guild, interaction.user, item_id)
    if not out.ok:
        retry = RetryView(bot, interaction.user, interaction.guild, item_id) if out.retryable else None
        return await view.interaction.response.edit_message(embed=_failure_embed(out, MAX_RETRIES if retry else None),
                                                            view=retry)
    await view.interaction.response.edit_message(embed=_success_embed(out, s), view=None)
    await interaction.followup.send(embed=ui.brand(ui.info(
        f"{ui.Icons.SHOP} عملية شراء", f"{interaction.user.mention} اشترى **{row['name']}** 🎉",
        ui.Colors.GOLD), interaction.guild))


async def inventory_embed(bot, guild: discord.Guild, member: discord.Member) -> discord.Embed:
    rows = await bot.db.user_purchases(guild.id, member.id)
    if not rows:
        return ui.info(f"🎒 مقتنيات {member.display_name}", "لا توجد مشتريات بعد.")
    lines = []
    for p in rows:
        exp = f" • ينتهي {ts(p['expires_at'])}" if p["expires_at"] and p["status"] == "delivered" else ""
        lines.append(f"`#{p['id']}` **{p['item_name']}** — {STATUS.get(p['status'], p['status'])}"
                     f"{exp} • {ts(p['created_at'], 'd')}")
    e = ui.info(f"🎒 مقتنيات {member.display_name}", "\n".join(lines), ui.Colors.INFO)
    e.set_thumbnail(url=member.display_avatar.url)
    return e


def product_embed(item, s: dict, balance: int) -> discord.Embed:
    """بطاقة تفاصيل منتج (صورته الكبيرة تُعرض من رابطها مباشرة عبر ديسكورد)."""
    e = ui.info(f"{ui.Icons.SHOP}  {item['name']}", item["description"] or None, ui.Colors.GOLD)
    kind = f"🎖️ رتبة <@&{item['role_id']}>" if item["kind"] == "role" else "🎁 مكافأة خاصة"
    e.add_field(name="السعر", value=ui.money(item["price"], s))
    e.add_field(name="النوع", value=kind)
    if item["duration_hours"]:
        h = item["duration_hours"]
        e.add_field(name="المدة", value=f"{h // 24} يوم" if h % 24 == 0 else f"{h} ساعة")
    e.add_field(name="المتوفر", value="غير محدود" if item["stock"] < 0 else ("نفد" if item["stock"] == 0
                                                                           else f"{item['stock']:,}"))
    e.add_field(name="رصيدك", value=ui.money(balance, s))
    if item["image_url"]:
        e.set_image(url=item["image_url"])
    e.set_footer(text=f"رقم المنتج #{item['id']}")
    return e


async def render_store(bot, guild: discord.Guild, member: discord.Member, page: int):
    """يبني رسالة المتجر: (Embed، ملف الصورة أو None، منتجات الصفحة، عدد الصفحات، الصفحة الفعلية)."""
    db = bot.db
    s = await db.get_settings(guild.id)
    items = await db.shop_items(guild.id)
    pages = max(1, (len(items) + STORE_PAGE - 1) // STORE_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = items[page * STORE_PAGE:(page + 1) * STORE_PAGE]
    bal = await db.balance(guild.id, member.id)
    e = ui.info(f"{ui.Icons.SHOP} متجر {guild.name}", None, ui.Colors.GOLD)
    e.set_footer(text=f"رصيدك: {bal:,} {s['currency_name']}  ·  صفحة {page + 1}/{pages}")
    file = None
    if cards.available():
        try:
            urls = {r["id"]: r["image_url"] for r in chunk if r["image_url"]}
            fetched = await asyncio.gather(*[images.fetch(u) for u in urls.values()])
            imgs = dict(zip(urls.keys(), fetched))
            png = await images.branded(
                guild, cards.render_store, items=[dict(id=r["id"], name=r["name"], price=r["price"], kind=r["kind"],
                                                stock=r["stock"], duration_hours=r["duration_hours"]) for r in chunk],
                images=imgs, balance=bal, currency=s["currency_name"], guild_name=guild.name, page=page + 1,
                pages=pages)
            file = discord.File(io.BytesIO(png), "store.png",
                                description="متجر السيرفر: " + "، ".join(f"{r['name']} {r['price']}" for r in chunk))
            if s["game_image_in_embed"]:
                e.set_image(url="attachment://store.png")
        except Exception:  # noqa: BLE001 - فشل الرسم لا يعطّل المتجر
            log.exception("Store card render failed; falling back to text")
            file = None
    if file is None:   # بديل نصي واضح
        e.description = "\n\n".join(item_line(i, s) for i in chunk) or "المتجر فارغ حاليًا."
    return e, file, chunk, pages, page


class StoreView(ui.BaseView):
    deny_message = "افتح المتجر بنفسك بالأمر /shop لتتصفحه وتشتري."

    def __init__(self, bot, owner: discord.Member, guild: discord.Guild, can_manage: bool):
        super().__init__(owner_id=owner.id, timeout=300)
        self.bot, self.guild, self.page, self.can_manage = bot, guild, 0, can_manage
        self.chunk: list = []
        self.pages = 1

    async def show(self, interaction: discord.Interaction, first: bool = False):
        member = interaction.user
        e, file, self.chunk, self.pages, self.page = await render_store(self.bot, self.guild, member, self.page)
        s = await self.bot.db.get_settings(self.guild.id)
        self.clear_items()
        if self.chunk:
            self.add_item(ProductSelect(self.chunk, s))
        self.add_item(_StoreBtn("prev", "السابق", "◀️", row=1, disabled=self.page == 0))
        self.add_item(_StoreBtn("next", "التالي", "▶️", row=1, disabled=self.page >= self.pages - 1))
        self.add_item(_StoreBtn("inventory", "مقتنياتي", "🎒", row=1))
        if self.can_manage:
            self.add_item(_StoreBtn("manage", "إدارة المتجر", "⚙️", discord.ButtonStyle.primary, row=1))
        kwargs = {"embed": e, "view": self}
        if first:
            if file:
                kwargs["file"] = file
            await interaction.followup.send(**kwargs)
            try:
                self.message = await interaction.original_response()
            except discord.HTTPException:
                pass
        else:
            kwargs["attachments"] = [file] if file else []
            await interaction.edit_original_response(**kwargs)


class _StoreBtn(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary, *, row: int,
                 disabled: bool = False):
        super().__init__(label=label, emoji=emoji, style=style, row=row, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        view: StoreView = self.view
        if self.action == "inventory":
            return await interaction.response.send_message(
                embed=await inventory_embed(view.bot, interaction.guild, interaction.user), ephemeral=True)
        if self.action == "manage":
            # فحص الصلاحية من الخادم عند الضغط (لا يكفي أن الزر ظهر)
            if not await has_category(view.bot.db, interaction.user, "shop"):
                return await interaction.response.send_message(
                    embed=ui.error("إدارة المتجر لأصحاب صلاحية المتجر فقط."), ephemeral=True)
            from cogs.panel import SECTIONS_BY_KEY, PanelView  # استيراد متأخر لتجنب الدوران
            pv = PanelView(view.bot, interaction.user, interaction.guild)
            pv.section = SECTIONS_BY_KEY["shop"]
            await interaction.response.send_message(embed=await pv.build(interaction.user), view=pv, ephemeral=True)
            pv.last_interaction = interaction
            return
        view.page += 1 if self.action == "next" else -1
        await interaction.response.defer()
        await view.show(interaction)


class ProductSelect(discord.ui.Select):
    def __init__(self, items, s: dict):
        super().__init__(placeholder="🛍️ اختر منتجًا لعرض تفاصيله وشرائه...", row=0, options=[
            discord.SelectOption(label=i["name"][:100], value=str(i["id"]), emoji="🎖️" if i["kind"] == "role" else "🎁",
                                 description=f"{i['price']:,} {s['currency_name']}"
                                             + (" — نفد" if i["stock"] == 0 else ""))
            for i in items])

    async def callback(self, interaction: discord.Interaction):
        view: StoreView = self.view
        db = view.bot.db
        item = await db.shop_item(interaction.guild_id, int(self.values[0]))
        if not item or not item["active"] or item["deleted"]:
            return await interaction.response.send_message(embed=ui.error("المنتج لم يعد متوفرًا."), ephemeral=True)
        s = await db.get_settings(interaction.guild_id)
        bal = await db.balance(interaction.guild_id, interaction.user.id)
        pv = ProductView(view.bot, interaction.user, item["id"], disabled=item["stock"] == 0)
        await interaction.response.send_message(embed=product_embed(item, s, bal), view=pv, ephemeral=True)


class ProductView(ui.BaseView):
    deny_message = "هذه نافذة شراء خاصة بصاحبها."

    def __init__(self, bot, owner: discord.Member, item_id: int, disabled: bool = False):
        super().__init__(owner_id=owner.id, timeout=180)
        self.bot, self.item_id = bot, item_id
        self.buy.disabled = disabled

    @discord.ui.button(label="شراء", emoji="🛒", style=discord.ButtonStyle.success)
    async def buy(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await purchase_flow(self.bot, interaction, self.item_id)


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.recover_loop.start()

    async def cog_unload(self):
        self.recover_loop.cancel()

    @tasks.loop(minutes=2)
    async def recover_loop(self):
        try:
            await recover_processing(self.bot)
        except Exception:  # noqa: BLE001 - لا نوقف المراقبة بسبب خطأ عابر
            log.exception("recover_processing failed")

    @recover_loop.before_loop
    async def _before_recover(self):
        await self.bot.wait_until_ready()

    async def item_autocomplete(self, interaction: discord.Interaction, current: str):
        items = await self.bot.db.shop_items(interaction.guild_id)
        s = await self.bot.db.get_settings(interaction.guild_id)
        return [
            app_commands.Choice(name=f"{i['name']} — {i['price']:,} {s['currency_name']}"[:100], value=i["id"])
            for i in items if current.lower() in i["name"].lower()
        ][:25]

    async def admin_item_autocomplete(self, interaction: discord.Interaction, current: str):
        items = await self.bot.db.shop_items(interaction.guild_id, include_inactive=True)
        return [
            app_commands.Choice(name=f"#{i['id']} {i['name']}" + ("" if i["active"] else " (مخفي)"), value=i["id"])
            for i in items if current.lower() in i["name"].lower()
        ][:25]

    # ------------------------------------------------------------------ للأعضاء
    @app_commands.command(name="shop", description="متجر السيرفر بتصميم مرئي: تصفّح واشترِ (وإدارته لأصحاب الصلاحية)")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction):
        can_manage = await has_category(self.bot.db, interaction.user, "shop")
        await interaction.response.defer()          # الرسم وتحميل الصور قد يتجاوز 3 ثوانٍ
        view = StoreView(self.bot, interaction.user, interaction.guild, can_manage)
        await view.show(interaction, first=True)

    @app_commands.command(name="buy", description="شراء منتج من المتجر")
    @app_commands.describe(item="المنتج")
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, item: int):
        await purchase_flow(self.bot, interaction, item)

    buy.autocomplete("item")(item_autocomplete)

    @app_commands.command(name="inventory", description="عرض مقتنياتك ومشترياتك")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        await ui.reply(interaction, await inventory_embed(self.bot, interaction.guild, member or interaction.user))

    # ------------------------------------------------------------------ للإدارة
    admin = app_commands.Group(name="shopadmin", description="إدارة المتجر", guild_only=True)

    @admin.command(name="add", description="إضافة منتج للمتجر")
    @app_commands.describe(
        name="اسم المنتج", price="السعر", kind="نوع المنتج", role="الرتبة (لمنتجات الرتب)",
        description="وصف المنتج", stock="الكمية (-1 غير محدود)",
        duration_hours="مدة الرتبة بالساعات (0 دائمة)", one_per_user="مرة واحدة لكل عضو؟",
        image_url="رابط صورة المنتج (https مباشر، اختياري)",
    )
    @require("shop")
    async def add(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 60],
                  price: app_commands.Range[int, 0, 1_000_000_000], kind: Literal["رتبة", "مكافأة خاصة"],
                  role: Optional[discord.Role] = None, description: Optional[app_commands.Range[str, 1, 300]] = None,
                  stock: app_commands.Range[int, -1, 1_000_000] = -1,
                  duration_hours: app_commands.Range[int, 0, 87600] = 0, one_per_user: bool = True,
                  image_url: Optional[app_commands.Range[str, 1, 400]] = None):
        try:
            image = images.validate_url(image_url or "")
        except ValueError as exc:
            return await ui.reply(interaction, ui.error(str(exc)), ephemeral=True)
        kind_key = "role" if kind == "رتبة" else "custom"
        if kind_key == "role":
            if not role:
                return await ui.reply(interaction, ui.error("حدد الرتبة لمنتج من نوع رتبة."), ephemeral=True)
            problem = role_sale_error(role, interaction.guild)
            if problem:
                return await ui.reply(interaction, ui.error(problem), ephemeral=True)
        elif duration_hours:
            duration_hours = 0
        item_id = await self.bot.db.add_shop_item(
            interaction.guild_id, name, description, price, kind_key, role.id if role else None,
            stock, duration_hours, one_per_user, image_url=image or None)
        s = await self.bot.db.get_settings(interaction.guild_id)
        item = await self.bot.db.shop_item(interaction.guild_id, item_id)
        await ui.reply(interaction, ui.success("تمت إضافة المنتج", item_line(item, s)))

    @admin.command(name="edit", description="تعديل منتج")
    @app_commands.describe(item="المنتج", visible="إظهار المنتج في المتجر",
                           image_url="رابط صورة جديد (https مباشر)")
    @require("shop")
    async def edit(self, interaction: discord.Interaction, item: int,
                   name: Optional[app_commands.Range[str, 1, 60]] = None,
                   price: Optional[app_commands.Range[int, 0, 1_000_000_000]] = None,
                   description: Optional[app_commands.Range[str, 1, 300]] = None,
                   stock: Optional[app_commands.Range[int, -1, 1_000_000]] = None,
                   duration_hours: Optional[app_commands.Range[int, 0, 87600]] = None,
                   one_per_user: Optional[bool] = None, visible: Optional[bool] = None,
                   image_url: Optional[app_commands.Range[str, 1, 400]] = None):
        db = self.bot.db
        existing = await db.shop_item(interaction.guild_id, item)
        if not existing or existing["deleted"]:
            return await ui.reply(interaction, ui.error("المنتج غير موجود."), ephemeral=True)
        try:
            image = images.validate_url(image_url) if image_url else None
        except ValueError as exc:
            return await ui.reply(interaction, ui.error(str(exc)), ephemeral=True)
        await db.update_shop_item(interaction.guild_id, item, name=name, price=price, description=description,
                                  stock=stock, duration_hours=duration_hours,
                                  one_per_user=None if one_per_user is None else int(one_per_user), image_url=image,
                                  active=None if visible is None else int(visible))
        s = await db.get_settings(interaction.guild_id)
        row = await db.shop_item(interaction.guild_id, item)
        status = "" if row["active"] else "\n\n🙈 المنتج مخفي من المتجر"
        await ui.reply(interaction, ui.success("تم تعديل المنتج", item_line(row, s) + status))

    @admin.command(name="remove", description="حذف منتج من المتجر (تبقى المشتريات السابقة محفوظة)")
    @require("shop")
    async def remove(self, interaction: discord.Interaction, item: int):
        row = await self.bot.db.shop_item(interaction.guild_id, item)
        if not row or row["deleted"]:
            return await ui.reply(interaction, ui.error("المنتج غير موجود."), ephemeral=True)
        await self.bot.db.delete_shop_item(interaction.guild_id, item)
        await ui.reply(interaction, ui.success("تم حذف المنتج", f"**{row['name']}** لم يعد معروضًا في المتجر "
                                                                "(المشتريات السابقة محفوظة)."))

    edit.autocomplete("item")(admin_item_autocomplete)
    remove.autocomplete("item")(admin_item_autocomplete)

    @admin.command(name="failures", description="آخر محاولات الشراء الفاشلة أو المستعادة (تتبع إداري)")
    @require("shop")
    async def failures(self, interaction: discord.Interaction):
        rows = await self.bot.db.purchase_attempts(interaction.guild_id)
        if not rows:
            return await ui.reply(interaction, ui.success("لا توجد إخفاقات", "كل عمليات الشراء مرّت بنجاح."),
                                  ephemeral=True)
        names = {"role_failed": "🔴 فشل منح الرتبة (عُكس)", "recovered": "🟢 استُكمل تلقائيًا", "reversed": "🟠 عُكس تلقائيًا"}
        lines = [f"`#{r['purchase_id'] or '—'}` <@{r['user_id']}> — {names.get(r['outcome'], r['outcome'])}\n"
                 f"└ {r['reason'] or ''} • <t:{r['created_at']}:R>" for r in rows]
        await ui.reply(interaction, ui.info("🧾 محاولات الشراء المتعثرة", "\n".join(lines), ui.Colors.WARNING),
                       ephemeral=True)

    @admin.command(name="pending", description="الطلبات بانتظار التسليم")
    @require("shop")
    async def pending(self, interaction: discord.Interaction):
        rows = await self.bot.db.pending_purchases(interaction.guild_id)
        if not rows:
            return await ui.reply(interaction, ui.success("لا توجد طلبات معلّقة"), ephemeral=True)
        lines = [f"`#{p['id']}` <@{p['user_id']}> — **{p['item_name']}** • {ts(p['created_at'])}" for p in rows]
        await ui.reply(interaction, ui.info("🟡 طلبات بانتظار التسليم", "\n".join(lines), ui.Colors.WARNING),
                       ephemeral=True)

    @admin.command(name="deliver", description="تأكيد تسليم طلب")
    @app_commands.describe(purchase_id="رقم الطلب")
    @require("shop")
    async def deliver(self, interaction: discord.Interaction, purchase_id: int):
        ok, msg = await deliver_purchase(self.bot, interaction.guild, interaction.user, purchase_id)
        await ui.reply(interaction, ui.success("تم التسليم", msg) if ok else ui.error(msg), ephemeral=not ok)

    @admin.command(name="refund", description="إلغاء طلب وإعادة النقاط للعضو")
    @app_commands.describe(purchase_id="رقم الطلب")
    @require("shop")
    async def refund(self, interaction: discord.Interaction, purchase_id: int):
        ok, msg = await refund_purchase(self.bot, interaction.guild, interaction.user, purchase_id)
        await ui.reply(interaction, ui.success("تم الاسترجاع", msg) if ok else ui.error(msg), ephemeral=not ok)


async def setup(bot):
    await bot.add_cog(Shop(bot))

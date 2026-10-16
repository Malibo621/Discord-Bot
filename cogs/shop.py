"""المتجر: عرض المنتجات، الشراء بالنقاط، المقتنيات، وإدارة المنتجات والطلبات."""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

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
}


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


async def purchase_flow(bot, interaction: discord.Interaction, item_id: int) -> None:
    """مسار الشراء الكامل (مشترك بين /buy وزر الشراء في المتجر): فحوص ← تأكيد ← خصم ذرّي ← تسليم."""
    db = bot.db
    s = await db.get_settings(interaction.guild_id)
    row = await db.shop_item(interaction.guild_id, item_id)
    if not row or not row["active"] or row["deleted"]:
        return await ui.reply(interaction, ui.error("المنتج غير موجود."), ephemeral=True)
    role = None
    if row["kind"] == "role":
        role = interaction.guild.get_role(row["role_id"])
        if not role:
            return await ui.reply(interaction, ui.error("رتبة هذا المنتج حُذفت. تواصل مع الإدارة."), ephemeral=True)
        if role >= interaction.guild.me.top_role:
            return await ui.reply(interaction, ui.error("لا أستطيع منح هذه الرتبة لأنها أعلى من رتبتي. "
                                                        "تواصل مع الإدارة."), ephemeral=True)
        if role in interaction.user.roles:
            return await ui.reply(interaction, ui.error("تملك هذه الرتبة مسبقًا."), ephemeral=True)
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

    try:   # الخصم والمخزون ومنع التكرار كلها داخل معاملة ذرّية واحدة في db.purchase
        purchase_id, new_bal = await db.purchase(interaction.guild_id, interaction.user.id, item_id)
    except InsufficientFunds:
        return await view.interaction.response.edit_message(embed=ui.error("رصيدك لم يعد كافيًا."), view=None)
    except LimitReached as exc:
        return await view.interaction.response.edit_message(embed=ui.error(exc.message), view=None)

    if role:
        try:
            await interaction.user.add_roles(role, reason=f"شراء من المتجر #{purchase_id}")
        except discord.HTTPException:
            await db.refund(interaction.guild_id, purchase_id, bot.user.id)
            return await view.interaction.response.edit_message(
                embed=ui.error("تعذّر منح الرتبة، وتمت إعادة نقاطك تلقائيًا."), view=None)
        delivery = f"تم منحك رتبة {role.mention}"
        row_p = await db.purchase_row(interaction.guild_id, purchase_id)
        if row_p["expires_at"]:
            delivery += f"، تنتهي {ts(row_p['expires_at'])}"
    else:
        delivery = "طلبك وصل للإدارة وسيتم تسليمه قريبًا."

    done = ui.success("تم الشراء بنجاح", f"**{row['name']}**\n{delivery}\n\n**رقم الطلب:** `#{purchase_id}`\n"
                                         f"**رصيدك الآن:** {ui.money(new_bal, s)}")
    await view.interaction.response.edit_message(embed=done, view=None)
    await interaction.followup.send(embed=ui.brand(ui.info(
        f"{ui.Icons.SHOP} عملية شراء", f"{interaction.user.mention} اشترى **{row['name']}** 🎉",
        ui.Colors.GOLD), interaction.guild))
    log_e = ui.info("🛒 عملية شراء" + (" — بانتظار التسليم" if not role else ""),
                    f"**العضو:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"**المنتج:** `#{row['id']}` {row['name']}\n**السعر:** `{row['price']:,}`\n"
                    f"**رقم الطلب:** `#{purchase_id}`" + ("" if role else "\n\nللتسليم: `/shopadmin deliver`"),
                    ui.Colors.WARNING if not role else ui.Colors.LOG)
    await bot.send_log(interaction.guild, "economy", log_e)


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
            png = await asyncio.to_thread(
                cards.render_store, items=[dict(id=r["id"], name=r["name"], price=r["price"], kind=r["kind"],
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

"""الهوية البصرية الموحّدة — كل رسائل البوت وواجهاته تمر من هنا لتبقى بنفس الشكل.

القواعد:
- لكل نوع رسالة لون ثابت وأيقونة واحدة في بداية العنوان فقط.
- الوصف قصير، والتفاصيل في حقول أو أسطر «مفتاح ← قيمة» مرتبة.
- الأرقام دائمًا بفواصل الآلاف وبخط عريض، والعملة دائمًا بجانب رمزها.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import discord

log = logging.getLogger("bot.ui")


class Colors:
    PRIMARY = 0x7C5CFF   # البنفسجي الأساسي للهوية
    SUCCESS = 0x2BD67B
    ERROR = 0xFF5C7A
    WARNING = 0xFFB547
    INFO = 0x4FA3FF
    PENDING = 0x8C7BFF
    GOLD = 0xF5C04A
    GAME = 0xA66BFF
    MUSIC = 0x1DB954
    MOD = 0xFF8A4C
    LOG = 0x8A93A6
    ADMIN = 0x5B6CFF


class Icons:
    SUCCESS = "✅"
    ERROR = "⛔"
    WARNING = "⚠️"
    INFO = "💠"
    PENDING = "⏳"
    MONEY = "🪙"
    SHOP = "🛒"
    GAME = "🎮"
    TROPHY = "🏆"
    CROWN = "👑"
    MUSIC = "🎵"
    SHIELD = "🛡️"
    HAMMER = "🔨"
    LOG = "📋"
    ADMIN = "🧭"
    TIMER = "⏱️"
    SPARK = "✨"


DOT = "•"
ARROW = "←"
LINE = "▬" * 14


def _base(title: str, description: Optional[str], color: int) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.timestamp = datetime.now(timezone.utc)
    return e


def success(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(f"{Icons.SUCCESS} {title}", description, Colors.SUCCESS)


def error(description: str, title: str = "تعذّر تنفيذ الطلب") -> discord.Embed:
    return _base(f"{Icons.ERROR} {title}", description, Colors.ERROR)


def warning(title: str, description: Optional[str] = None) -> discord.Embed:
    return _base(f"{Icons.WARNING} {title}", description, Colors.WARNING)


def pending(title: str, description: Optional[str] = None) -> discord.Embed:
    """حالة انتظار/معالجة."""
    return _base(f"{Icons.PENDING} {title}", description, Colors.PENDING)


def info(title: str, description: Optional[str] = None, color: int = Colors.PRIMARY) -> discord.Embed:
    return _base(title, description, color)


def brand(embed: discord.Embed, guild: Optional[discord.Guild], extra: Optional[str] = None) -> discord.Embed:
    if guild:
        text = guild.name + (f"  {DOT}  {extra}" if extra else "")
        embed.set_footer(text=text, icon_url=guild.icon.url if guild.icon else None)
    elif extra:
        embed.set_footer(text=extra)
    return embed


# ---------------------------------------------------------------------- تنسيق القيم
def money(amount: int, settings: dict) -> str:
    return f"**{amount:,}** {settings['currency_emoji']} {settings['currency_name']}"


def delta(amount: int, settings: dict) -> str:
    """عرض تغيّر الرصيد: +50 🪙 أو −20 🪙."""
    sign = "+" if amount >= 0 else "−"
    return f"`{sign}{abs(amount):,}` {settings['currency_emoji']}"


def kv(pairs: Iterable[tuple[str, str]]) -> str:
    """أسطر «العنوان ← القيمة» مرتبة — أوضح على الجوال من الحقول المتجاورة."""
    return "\n".join(f"**{k}** {ARROW} {v}" for k, v in pairs)


def progress_bar(value: int, maximum: int, length: int = 12) -> str:
    if maximum <= 0:
        return "▱" * length
    filled = max(0, min(length, round(length * value / maximum)))
    return "▰" * filled + "▱" * (length - filled)


def countdown(unix_ts: int) -> str:
    return f"<t:{unix_ts}:R>"


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def rank_icon(position: int) -> str:
    return MEDALS.get(position, f"`#{position}`")


# ---------------------------------------------------------------------- الإرسال الآمن
async def reply(interaction: discord.Interaction, embed: discord.Embed, *, ephemeral: bool = False,
                view: Optional[discord.ui.View] = None, file: Optional[discord.File] = None,
                content: Optional[str] = None) -> None:
    """يرد على التفاعل سواء كان أول رد أو متابعة، ويتجاهل التفاعلات المنتهية بهدوء."""
    kwargs = {"embed": embed, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if content is not None:
        kwargs["content"] = content
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.NotFound:
        # انتهت صلاحية التفاعل (أكثر من 3 ثوانٍ بلا رد أو 15 دقيقة للمتابعة)
        log.debug("Interaction expired before reply")


async def safe_edit(message: Optional[discord.Message], **kwargs) -> bool:
    if message is None:
        return False
    try:
        await message.edit(**kwargs)
        return True
    except discord.HTTPException:
        return False


async def safe_send(channel: discord.abc.Messageable, **kwargs) -> Optional[discord.Message]:
    try:
        return await channel.send(**kwargs)
    except discord.HTTPException:
        return None


# ---------------------------------------------------------------------- الواجهات
class BaseView(discord.ui.View):
    """واجهة أساس: تقييد على صاحبها (اختياري)، تعطيل الأزرار عند الانتهاء، ومعالجة الأخطاء."""

    deny_message = "هذه الواجهة ليست لك."

    def __init__(self, *, owner_id: Optional[int] = None, timeout: Optional[float] = 180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and interaction.user.id != self.owner_id:
            await interaction.response.send_message(embed=error(self.deny_message, "غير مسموح"), ephemeral=True)
            return False
        return True

    def disable_all(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True

    async def on_timeout(self) -> None:
        self.disable_all()
        await safe_edit(self.message, view=self)

    async def on_error(self, interaction: discord.Interaction, exc: Exception, item: discord.ui.Item) -> None:
        log.exception("View error in %s", type(self).__name__, exc_info=exc)
        msg = ("لا أملك الصلاحية الكافية لهذا الإجراء." if isinstance(exc, discord.Forbidden)
               else "حدث خطأ غير متوقع أثناء تنفيذ هذا الإجراء.")
        try:
            await reply(interaction, error(msg), ephemeral=True)
        except discord.HTTPException:
            pass


class ConfirmView(BaseView):
    """أزرار تأكيد/إلغاء خاصة بصاحب الأمر فقط. تُستخدم مرة واحدة."""

    deny_message = "هذه الأزرار ليست لك."

    def __init__(self, owner_id: int, timeout: float = 30):
        super().__init__(owner_id=owner_id, timeout=timeout)
        self.value: Optional[bool] = None
        self.interaction: Optional[discord.Interaction] = None

    async def _resolve(self, interaction: discord.Interaction, value: bool):
        if self.value is not None:  # منع الضغط المزدوج
            return await interaction.response.defer()
        self.value = value
        self.interaction = interaction
        self.stop()

    @discord.ui.button(label="تأكيد", style=discord.ButtonStyle.success, emoji="✔️")
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._resolve(interaction, True)

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self._resolve(interaction, False)


class Paginator(BaseView):
    """تصفح صفحات مضمّنة بأزرار."""

    deny_message = "استخدم الأمر بنفسك لتتصفح."

    def __init__(self, pages: list[discord.Embed], owner_id: int, timeout: float = 120):
        super().__init__(owner_id=owner_id, timeout=timeout)
        self.pages = pages
        self.index = 0
        for i, p in enumerate(pages):
            footer = p.footer.text or ""
            p.set_footer(text=f"صفحة {i + 1} من {len(pages)}" + (f"  {DOT}  {footer}" if footer else ""),
                         icon_url=p.footer.icon_url)
        self._sync()

    def _sync(self):
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    @discord.ui.button(emoji="◀️", label="السابق", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _b: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(emoji="▶️", label="التالي", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _b: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self):
        await safe_edit(self.message, view=None)

    async def start(self, interaction: discord.Interaction, ephemeral: bool = False):
        view = self if len(self.pages) > 1 else None
        await reply(interaction, self.pages[0], ephemeral=ephemeral, view=view)
        if view:
            try:
                self.message = await interaction.original_response()
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------- البطاقات المصوّرة (إنفوجرافيك)
def card_file(png: bytes, name: str, alt: str) -> discord.File:
    import io
    return discord.File(io.BytesIO(png), name, description=(alt or "")[:1000] or None)


async def reply_card(interaction: discord.Interaction, png: bytes, name: str, alt: str, *, ephemeral: bool = False,
                     view: Optional[discord.ui.View] = None, in_embed: bool = False,
                     embed: Optional[discord.Embed] = None) -> None:
    """يرسل بطاقة مصوّرة: مرفقًا مستقلًا (يُعرض أكبر في ديسكورد) أو داخل Embed حسب الإعداد."""
    file = card_file(png, name, alt)
    kwargs: dict = {"file": file, "ephemeral": ephemeral}
    if view is not None:
        kwargs["view"] = view
    if in_embed:
        embed = embed or discord.Embed(color=Colors.PRIMARY)
        embed.set_image(url=f"attachment://{name}")
    if embed is not None:
        kwargs["embed"] = embed
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.NotFound:
        log.debug("Interaction expired before card reply")


class ImagePaginator(BaseView):
    """تصفح صفحات مصوّرة: كل صفحة تُرسم عند الطلب (مرة واحدة) وتُخزَّن لتجنب إعادة التوليد."""

    deny_message = "استخدم الأمر بنفسك لتتصفح."

    def __init__(self, owner_id: int, pages: int, render, *, timeout: float = 180, in_embed: bool = False,
                 name: str = "card.png"):
        super().__init__(owner_id=owner_id, timeout=timeout)
        self.pages, self.render, self.index = pages, render, 0   # render(index) -> (png bytes, alt text)
        self.in_embed, self.name = in_embed, name
        self._cache: dict[int, tuple[bytes, str]] = {}
        self._sync()

    def _sync(self):
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= self.pages - 1
        if self.pages <= 1:
            self.clear_items()

    async def page(self, index: int) -> tuple[bytes, str]:
        if index not in self._cache:
            self._cache[index] = await self.render(index)
        return self._cache[index]

    def _kwargs(self, png: bytes, alt: str) -> dict:
        kw = {"attachments": [card_file(png, self.name, alt)], "view": self if self.pages > 1 else None}
        if self.in_embed:
            e = discord.Embed(color=Colors.PRIMARY)
            e.set_image(url=f"attachment://{self.name}")
            kw["embed"] = e
        return kw

    async def _go(self, interaction: discord.Interaction, delta: int):
        self.index = max(0, min(self.pages - 1, self.index + delta))
        self._sync()
        await interaction.response.defer()
        png, alt = await self.page(self.index)
        await interaction.edit_original_response(**self._kwargs(png, alt))

    @discord.ui.button(emoji="◀️", label="السابق", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await self._go(interaction, -1)

    @discord.ui.button(emoji="▶️", label="التالي", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _b: discord.ui.Button):
        await self._go(interaction, 1)

    async def on_timeout(self):
        await safe_edit(self.message, view=None)

    async def start(self, interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
        png, alt = await self.page(0)
        kwargs: dict = {"file": card_file(png, self.name, alt), "ephemeral": ephemeral}
        if self.pages > 1:
            kwargs["view"] = self
        if self.in_embed:
            e = discord.Embed(color=Colors.PRIMARY)
            e.set_image(url=f"attachment://{self.name}")
            kwargs["embed"] = e
        try:
            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
            if self.pages > 1:
                self.message = await interaction.original_response()
        except discord.NotFound:
            pass

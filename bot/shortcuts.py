"""الأوامر النصية المختصرة (مثل !ban) كاختصارات لأوامر Slash الموجودة.

أوامر Slash لا تُستدعى بدون «/» (قيد من ديسكورد)، لذلك تُقرأ الرسائل التي تبدأ بالبادئة (الافتراضي !)،
فإن طابق أول كلمة اختصارًا محفوظًا حُوّلت بقية الرسالة إلى وسائط الأمر الأصلي ونُفّذ الأمر نفسه عبر واجهة
تفاعل بديلة (TextInteraction)، فتُطبَّق عليه فحوصاته الأصلية كلها: الصلاحيات، صلاحيات البوت، والتعطيل.
"""
from __future__ import annotations

import re
import typing
from types import SimpleNamespace
from typing import Any, Optional

import discord
from discord import app_commands
from discord.enums import AppCommandOptionType as T

from . import registry, ui
from .settings_schema import FALSE_WORDS, TRUE_WORDS

NAME_RE = re.compile(r"^[a-z0-9_\-\u0600-\u06FF]{1,20}$")
MAX_SHORTCUTS = 60
EPHEMERAL_TTL = 25


class ShortcutError(Exception):
    """خطأ في كتابة الأمر النصي (وسيط ناقص أو غير صالح). الرسالة تُعرض للمستخدم."""


class TypeMismatch(ShortcutError):
    """الكلمة ليست من نوع الوسيط (يُتخطى إن كان الوسيط اختياريًا)."""


def validate_shortcut(tree, table: dict[str, str], raw: str, cmd, *, replacing: Optional[str] = None) -> str:
    """يتحقق من اسم الاختصار ويرجع صيغته الموحّدة أو يرفع ValueError برسالة عربية."""
    name = (raw or "").strip().lstrip("/!.?").lower()
    if not NAME_RE.match(name):
        raise ValueError("اسم الاختصار: كلمة واحدة من 1 إلى 20 حرفًا (حروف عربية/إنجليزية صغيرة أو أرقام أو `_` أو `-`).")
    ok, reason = registry.shortcut_support(cmd)
    if not ok:
        raise ValueError(reason)
    if name in table and name != replacing:
        raise ValueError(f"الاختصار `{name}` مستخدم مسبقًا للأمر `/{table[name]}`.")
    if replacing is None and len(table) >= MAX_SHORTCUTS:
        raise ValueError(f"بلغت الحد الأقصى ({MAX_SHORTCUTS}) اختصارًا.")
    own = {cmd.name, cmd.qualified_name.replace(" ", "-")}
    for other in registry.leaves(tree):
        if other.qualified_name == cmd.qualified_name:
            continue
        names = {other.name, other.qualified_name.replace(" ", "-"), other.qualified_name.split()[0]}
        if name in names and name not in own:
            raise ValueError(f"`{name}` يطابق اسم الأمر `/{other.qualified_name}`؛ اختر اسمًا مختلفًا لتجنب اللبس.")
    return name


# ---------------------------------------------------------------------- واجهة التفاعل البديلة
class _Response:
    def __init__(self, parent: "TextInteraction"):
        self._p = parent
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False, thinking: bool = False) -> None:
        self._done = True

    async def send_message(self, content: Optional[str] = None, **kwargs) -> None:
        self._done = True
        await self._p._send(content, **kwargs)

    async def edit_message(self, **kwargs) -> None:
        self._done = True
        await self._p.edit_original_response(**kwargs)

    async def send_modal(self, *_a, **_k) -> None:
        raise ShortcutError("هذا الأمر يحتاج نافذة إدخال ولا يعمل بالرسائل النصية. استخدم أمر Slash.")


class _Followup:
    def __init__(self, parent: "TextInteraction"):
        self._p = parent

    async def send(self, content: Optional[str] = None, **kwargs):
        return await self._p._send(content, **kwargs)


class TextInteraction:
    """يحاكي الجزء المستخدم من discord.Interaction فوق رسالة نصية عادية."""

    def __init__(self, bot, message: discord.Message, command):
        self.client = bot
        self.message = message
        self.command = command
        self.user = message.author
        self.guild = message.guild
        self.guild_id = message.guild.id if message.guild else None
        self.channel = message.channel
        self.channel_id = message.channel.id
        self.namespace = SimpleNamespace()
        self.data: dict = {}
        self.id = message.id
        self.type = discord.InteractionType.application_command
        self.locale = discord.Locale.american_english
        self.guild_locale = None
        self.created_at = message.created_at
        self.response = _Response(self)
        self.followup = _Followup(self)
        self.is_text_shortcut = True
        self._last: Optional[discord.Message] = None
        self._first = True

    @property
    def app_permissions(self) -> discord.Permissions:
        return self.channel.permissions_for(self.guild.me)

    @property
    def permissions(self) -> discord.Permissions:
        return self.channel.permissions_for(self.user)

    async def _send(self, content: Optional[str] = None, *, ephemeral: bool = False, wait: bool = True,
                    **kwargs) -> Optional[discord.Message]:
        allowed = {"embed", "embeds", "view", "file", "files", "allowed_mentions", "tts"}
        kw = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if ephemeral:  # لا وجود للردود الخاصة في الرسائل النصية: تُحذف تلقائيًا بعد وقت قصير
            kw["delete_after"] = EPHEMERAL_TTL
        try:
            if self._first:
                msg = await self.message.reply(content, mention_author=False, **kw)
            else:
                msg = await self.channel.send(content, **kw)
        except discord.HTTPException:
            try:
                msg = await self.channel.send(content, **kw)
            except discord.HTTPException:
                return None
        self._first = False
        self._last = msg
        return msg

    async def original_response(self) -> discord.Message:
        if self._last is None:
            raise discord.NotFound(SimpleNamespace(status=404, reason="not found"), "no response yet")
        return self._last

    async def edit_original_response(self, **kwargs) -> Optional[discord.Message]:
        if self._last is None:
            return await self._send(kwargs.pop("content", None), **kwargs)
        kw = {k: v for k, v in kwargs.items() if k in {"content", "embed", "embeds", "view", "attachments"}}
        self._last = await self._last.edit(**kw)
        return self._last


# ---------------------------------------------------------------------- تحليل الوسائط
def _spans(text: str) -> list[tuple[str, int, int]]:
    """(الكلمة، بدايتها، نهايتها) مع دعم النص بين علامتي تنصيص "..." أو «...»."""
    out: list[tuple[str, int, int]] = []
    buf: list[str] = []
    start = 0
    quote: Optional[str] = None
    closers = {'"': '"', "“": "”", "«": "»"}
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                out.append(("".join(buf), start, i + 1))
                buf, quote = [], None
            else:
                buf.append(ch)
        elif ch in closers and not buf:
            quote, start = closers[ch], i
        elif ch.isspace():
            if buf:
                out.append(("".join(buf), start, i))
                buf = []
        else:
            if not buf:
                start = i
            buf.append(ch)
    if buf or quote:
        out.append(("".join(buf), start, len(text)))
    return out


def tokenize(text: str) -> list[tuple[str, int]]:
    return [(t, a) for t, a, _b in _spans(text)]


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٬٫", "0123456789,.")


def _to_int(tok: str) -> int:
    return int(tok.translate(_AR_DIGITS).replace(",", ""))


def _id_from(tok: str, kinds: str) -> Optional[int]:
    m = re.fullmatch(rf"<(?:{kinds})(\d+)>", tok) or re.fullmatch(r"(\d{15,21})", tok)
    return int(m.group(1)) if m else None


def _hints(cmd) -> dict[str, Any]:
    try:
        return typing.get_type_hints(cmd.callback)
    except Exception:  # noqa: BLE001 - تلميحات غير قابلة للحل: نتجاهلها
        return {}


def _accepts_user(hint: Any) -> bool:
    return hint is not None and (hint is discord.User or discord.User in typing.get_args(hint))


async def _member(message: discord.Message, tok: str, allow_user: bool, client=None):
    guild = message.guild
    uid = _id_from(tok, "@!?")
    if uid:
        m = guild.get_member(uid)
        if m:
            return m
        try:
            return await guild.fetch_member(uid)
        except discord.HTTPException:
            pass
        if allow_user and client is not None:
            try:
                return await client.fetch_user(uid)
            except discord.HTTPException:
                pass
        raise ShortcutError("لم أجد العضو. اذكره بالمنشن أو الآيدي.")
    name = tok.lstrip("@").casefold()
    found = [m for m in guild.members if name in (m.display_name.casefold(), m.name.casefold())]
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        raise ShortcutError(f"يوجد أكثر من عضو باسم `{tok}`، استخدم المنشن أو الآيدي.")
    raise ShortcutError("لم أجد العضو. اذكره بالمنشن أو الآيدي.")


def _by_name_or_id(items, tok: str, kinds: str, label: str):
    rid = _id_from(tok, kinds)
    if rid:
        for it in items:
            if it.id == rid:
                return it
    else:
        name = tok.lstrip("#@").casefold()
        found = [it for it in items if it.name.casefold() == name]
        if len(found) == 1:
            return found[0]
    raise ShortcutError(f"لم أجد {label} `{tok}`.")


async def _convert(message: discord.Message, p, tok: str, hint: Any, client=None):
    t = p.type
    if t == T.string:
        value: Any = tok
        if p.choices:
            for c in p.choices:
                if tok.casefold() in (str(c.name).casefold(), str(c.value).casefold()):
                    return c.value
            raise TypeMismatch("اختر أحد: " + "، ".join(f"`{c.name}`" for c in p.choices))
        if p.min_value is not None and len(tok) < p.min_value:
            raise ShortcutError(f"النص قصير جدًا (الحد الأدنى {int(p.min_value)} أحرف).")
        if p.max_value is not None and len(tok) > p.max_value:
            raise ShortcutError(f"النص طويل جدًا (الحد الأقصى {int(p.max_value)} حرفًا).")
        return value
    if t in (T.integer, T.number):
        try:
            value = _to_int(tok) if t == T.integer else float(tok.translate(_AR_DIGITS).replace(",", ""))
        except ValueError:
            raise TypeMismatch("القيمة يجب أن تكون رقمًا.") from None
        if p.choices:
            for c in p.choices:
                if value == c.value or tok.casefold() == str(c.name).casefold():
                    return c.value
            raise TypeMismatch("اختر أحد: " + "، ".join(f"`{c.name}`" for c in p.choices))
        lo, hi = p.min_value, p.max_value
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ShortcutError(f"الرقم يجب أن يكون بين {lo:g} و {hi:g}." if lo is not None and hi is not None
                                else f"الرقم يجب أن يكون {'لا يقل عن ' + format(lo, 'g') if lo is not None else 'لا يزيد على ' + format(hi, 'g')}.")
        return value
    if t == T.boolean:
        low = tok.lower()
        if low in TRUE_WORDS:
            return True
        if low in FALSE_WORDS:
            return False
        raise TypeMismatch("اكتب نعم أو لا.")
    if t == T.user:
        return await _member(message, tok, _accepts_user(hint), client)
    if t == T.role:
        return _by_name_or_id(message.guild.roles, tok, "@&", "الرتبة")
    if t == T.channel:
        return _by_name_or_id(message.guild.channels, tok, "#", "الروم")
    if t == T.mentionable:
        try:
            return await _member(message, tok, False)
        except ShortcutError:
            return _by_name_or_id(message.guild.roles, tok, "@&", "العضو أو الرتبة")
    raise ShortcutError("نوع الوسيط غير مدعوم نصيًا.")


def usage(cmd, prefix: str, alias: str) -> str:
    parts = " ".join(f"<{p.display_name}>" if p.required else f"[{p.display_name}]" for p in cmd.parameters)
    return f"`{prefix}{alias}{(' ' + parts) if parts else ''}`"


async def parse_arguments(client, message: discord.Message, cmd, text: str) -> dict[str, Any]:
    """يحوّل نص الرسالة لوسائط الأمر الأصلي. آخر وسيط نصي يأخذ بقية الرسالة كما هي (سبب، ملاحظة...)."""
    params = list(cmd.parameters)
    hints = _hints(cmd)
    spans = _spans(text)
    idx = 0
    kwargs: dict[str, Any] = {}
    for i, p in enumerate(params):
        if idx >= len(spans):
            if p.required:
                raise ShortcutError(f"الوسيط **{p.display_name}** مطلوب.")
            continue
        if p.type == T.string and not p.choices and i == len(params) - 1:
            value = text[spans[idx][1]:].strip()
            if len(spans) - idx == 1 and len(value) > 1 and value[0] in '"“«' and value[-1] in '"”»':
                value = value[1:-1]
            idx = len(spans)
            if p.max_value is not None and len(value) > p.max_value:
                raise ShortcutError(f"**{p.display_name}**: النص طويل جدًا (الحد الأقصى {int(p.max_value)} حرفًا).")
            if p.min_value is not None and len(value) < p.min_value:
                raise ShortcutError(f"**{p.display_name}**: النص قصير جدًا (الحد الأدنى {int(p.min_value)} أحرف).")
            kwargs[p.name] = value
            continue
        tok = spans[idx][0]
        idx += 1
        try:
            kwargs[p.name] = await _convert(message, p, tok, hints.get(p.name), client)
        except TypeMismatch as exc:
            if not p.required:
                idx -= 1                       # وسيط اختياري لا يناسبه هذا النص: نتخطاه ونجرّب التالي
                continue
            raise ShortcutError(f"**{p.display_name}**: {exc}") from None
        except ShortcutError as exc:
            raise ShortcutError(f"**{p.display_name}**: {exc}") from None
    if idx < len(spans):
        raise ShortcutError("عدد الوسائط أكثر من المطلوب.")
    return kwargs


# ---------------------------------------------------------------------- الاكتشاف والتنفيذ
async def find_target(bot, message: discord.Message):
    """يرجع (الاختصار، الأمر، بقية النص، البادئة) إذا كانت الرسالة اختصارًا صالحًا، وإلا None."""
    if message.author.bot or message.guild is None or not message.content:
        return None
    s = await bot.db.get_settings(message.guild.id)
    prefix = s["shortcut_prefix"]
    if not s["shortcuts_enabled"] or not prefix or not message.content.startswith(prefix):
        return None
    head, _, rest = message.content[len(prefix):].partition(" ")
    name = head.strip().lower()
    if not name:
        return None
    target = (await bot.db.shortcuts(message.guild.id)).get(name)
    if target is None:
        return None                      # ليس اختصارًا: تجاهل صامت حتى لا نزعج الكلام العادي
    cmd = registry.find(bot.tree, target)
    if cmd is None:
        return None
    return name, cmd, rest.strip(), prefix


async def run(bot, message: discord.Message, name: str, cmd, rest: str, prefix: str) -> None:
    """ينفّذ الأمر الأصلي بكل فحوصاته. أي خطأ يُعرض للمستخدم ولا يوقف البوت."""
    ti = TextInteraction(bot, message, cmd)
    try:
        ok, reason = registry.shortcut_support(cmd)
        if not ok:
            raise ShortcutError(reason)
        await bot.tree.interaction_check(ti)                 # التعطيل (يرفع CommandDisabled)
        if not await cmd._check_can_run(ti):                 # الصلاحيات الأصلية وصلاحيات البوت
            raise app_commands.CheckFailure("check failed")
        req = cmd.default_permissions
        if req is not None and not message.author.guild_permissions.is_superset(req):
            raise app_commands.MissingPermissions([n for n, v in req if v])
        kwargs = await parse_arguments(bot, message, cmd, rest)
        args = (cmd.binding, ti) if cmd.binding is not None else (ti,)
        await cmd.callback(*args, **kwargs)
    except ShortcutError as exc:
        text = f"{exc}\n\n**طريقة الاستخدام:** {usage(cmd, prefix, name)}"
        await ti._send(None, embed=ui.error(text, "تعذّر تنفيذ الأمر"), ephemeral=True)
    except app_commands.AppCommandError as exc:
        await bot.on_app_command_error(ti, exc)
    except Exception as exc:  # noqa: BLE001
        await bot.on_app_command_error(ti, app_commands.CommandInvokeError(cmd, exc))

"""سجل الأوامر: يقرأ أوامر Slash المسجّلة فعليًا، يوضّح أوصافها وصلاحياتها، ويطبّق تخصيصات الإدارة.

ثلاثة أنواع من التعديل — ولكل منها أثر مختلف (وهذا ما تشرحه لوحة /commands للمسؤول):
1) تفعيل/تعطيل منطقي: يُخزَّن في قاعدة البيانات ويُفحص عند كل تنفيذ (فوري). الأمر يبقى ظاهرًا في قائمة ديسكورد
   لكنه يرد برسالة «معطّل». لا يحتاج مزامنة.
2) وصف الأمر/الخيار: هو نص مسجّل لدى ديسكورد، فلا يتغير عند المستخدمين إلا بعد «تطبيق على ديسكورد» (مزامنة).
3) الاختصارات النصية: محلية بالكامل داخل البوت (فورية)، ولا علاقة لها بقائمة ديسكورد.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import discord
from discord import app_commands

from .settings_schema import PERMISSION_CATEGORIES

log = logging.getLogger("bot.registry")

# أوامر لا يجوز تعطيلها (قفل المسؤول خارج اللوحة) ولا يُنشأ لها اختصار
PROTECTED = {"panel", "commands", "help"}
# أوامر لا تُحوَّل لاختصارات نصية: إدارة البوت نفسه، أو تحتاج سياق صوتي/نوافذ تفاعلية
NO_SHORTCUT_ROOTS = {"panel", "commands", "settings", "permissions", "music"}

DISCORD_DESC_MAX = 100

# ---------------------------------------------------------------------- أوصاف واضحة
COMMAND_DOCS: dict[str, str] = {
    "game roulette": "عجلة روليت بأسماء المشاركين: تدور وتختار الفائز، أو نمط إقصاء على جولات",
    "game list": "عرض الألعاب المتاحة وجوائزها ومددها",
    "game fast": "تحدي سرعة: سؤال تجيب عليه بالكتابة وأول إجابة صحيحة تفوز (أو اكتب -اسرع)",
    "points reset": "تصفير رصيد عضو (يُسجَّل في سجل الاقتصاد)",
}

_REWARD = "جائزة مخصصة بالنقاط للفعالية (لمنظمي الفعاليات فقط، وتتجاوز الجائزة الافتراضية)"
OPTION_DOCS: dict[tuple[str, str], str] = {
    ("game roulette", "mode"): "نمط اللعبة: «عجلة الحظ» تدور مرة وتختار الفائز، «إقصاء» جولات حتى النهائي",
    ("game roulette", "join_seconds"): "مدة التسجيل بالثواني قبل أن تدور العجلة (15–180). الافتراضي من الإعدادات",
    ("game roulette", "reward"): _REWARD,
    ("game typing", "reward"): _REWARD,
    ("game scramble", "reward"): _REWARD,
    ("game math", "reward"): _REWARD,
    ("game quiz", "reward"): _REWARD,
    ("game guess", "reward"): _REWARD,
    ("game fast", "reward"): _REWARD,
    ("game guess", "maximum"): "أكبر رقم ممكن للرقم السري (من 10 إلى 1000)",
    ("settings reset", "key"): "الإعداد الذي تريد إرجاعه لقيمته الافتراضية",
    ("permissions remove", "category"): "فئة الأوامر التي ستُسحب منها الصلاحية",
    ("permissions remove", "role"): "الرتبة التي ستفقد صلاحية هذه الفئة",
    ("points reset", "member"): "العضو الذي سيُصفَّر رصيده",
    ("points reset", "reason"): "سبب التصفير (يظهر في سجل الاقتصاد)",
    ("inventory", "member"): "العضو الذي تريد رؤية مقتنياته (الافتراضي: أنت)",
    ("shopadmin edit", "name"): "الاسم الجديد للمنتج",
    ("shopadmin edit", "price"): "السعر الجديد بالنقاط",
    ("shopadmin edit", "description"): "الوصف الجديد للمنتج",
    ("shopadmin edit", "stock"): "الكمية الجديدة (-1 = غير محدودة)",
    ("shopadmin edit", "duration_hours"): "مدة الرتبة الجديدة بالساعات (0 = دائمة)",
    ("shopadmin edit", "one_per_user"): "هل يُباع للعضو مرة واحدة فقط؟",
    ("shopadmin remove", "item"): "المنتج الذي سيُخفى من المتجر (تبقى المشتريات السابقة محفوظة)",
    ("game add-word", "word"): "الكلمة الجديدة لألعاب الكتابة والتفكيك (2–30 حرفًا)",
    ("game remove-content", "content_id"): "رقم الكلمة أو السؤال المراد حذفه (يظهر في /game content)",
    ("music volume", "percent"): "مستوى الصوت بالنسبة المئوية (1–150)",
    ("warnings", "member"): "العضو الذي تريد عرض تحذيراته",
    ("delwarn", "reason"): "سبب إلغاء التحذير (اختياري، يُسجَّل في سجل الإدارة)",
    ("clearwarns", "reason"): "سبب إلغاء التحذيرات (اختياري، يُسجَّل في سجل الإدارة)",
    ("delwarn", "warn_id"): "رقم التحذير المراد حذفه (يظهر في /warnings)",
    ("clearwarns", "member"): "العضو الذي ستُمسح كل تحذيراته",
    ("unmute", "member"): "العضو الذي سيُفك كتمه",
    ("unmute", "reason"): "سبب فك الكتم (يظهر في سجل الإدارة)",
    ("kick", "member"): "العضو الذي سيُطرد من السيرفر",
    ("kick", "reason"): "سبب الطرد (يظهر في سجل الإدارة)",
    ("unban", "user_id"): "آيدي المستخدم المحظور (الأرقام فقط)",
    ("unban", "reason"): "سبب فك الحظر (يظهر في سجل الإدارة)",
    ("lock", "reason"): "سبب قفل الروم (يظهر للأعضاء)",
    ("userinfo", "member"): "العضو المطلوب (الافتراضي: أنت)",
    ("avatar", "member"): "العضو المطلوب (الافتراضي: أنت)",
}

TYPE_LABELS = {"general": "عام", "games": "ألعاب", "admin": "إداري"}
OPTION_TYPE_LABELS = {
    discord.AppCommandOptionType.string: "نص",
    discord.AppCommandOptionType.integer: "رقم صحيح",
    discord.AppCommandOptionType.boolean: "نعم/لا",
    discord.AppCommandOptionType.user: "عضو",
    discord.AppCommandOptionType.channel: "روم",
    discord.AppCommandOptionType.role: "رتبة",
    discord.AppCommandOptionType.mentionable: "عضو أو رتبة",
    discord.AppCommandOptionType.number: "رقم عشري",
    discord.AppCommandOptionType.attachment: "ملف",
}


@dataclass
class OptionInfo:
    name: str
    description: str
    required: bool
    type_label: str
    choices: list[str] = field(default_factory=list)


@dataclass
class CommandInfo:
    qualified: str
    description: str
    default_description: str
    kind: str                      # general | games | admin
    perm: Optional[str]            # None | admin | اسم فئة
    perm_label: str
    options: list[OptionInfo]
    enabled: bool
    protected: bool
    shortcuts: list[str]
    shortcut_ok: bool
    shortcut_reason: str
    group: str

    @property
    def kind_label(self) -> str:
        return TYPE_LABELS[self.kind]


# الأوصاف الأصلية (قبل أي تخصيص) تُلتقط مرة واحدة عند أول تطبيق
_DEFAULTS: dict[tuple[str, Optional[str]], str] = {}


def leaves(tree: app_commands.CommandTree) -> list[app_commands.Command]:
    return sorted((c for c in tree.walk_commands() if isinstance(c, app_commands.Command)),
                  key=lambda c: c.qualified_name)


def find(tree: app_commands.CommandTree, qualified: str) -> Optional[app_commands.Command]:
    for c in tree.walk_commands():
        if isinstance(c, app_commands.Command) and c.qualified_name == qualified:
            return c
    return None


def _all_checks(cmd: app_commands.Command) -> list:
    out, node = list(cmd.checks), cmd.parent
    while node is not None:
        out.extend(getattr(node, "checks", []))
        node = getattr(node, "parent", None)
    return out


def required_perm(cmd: app_commands.Command) -> Optional[str]:
    perms = [getattr(c, "perm_category", None) for c in _all_checks(cmd)]
    perms = [p for p in perms if p]
    if "admin" in perms:
        return "admin"
    return perms[0] if perms else None


def perm_label(cmd: app_commands.Command) -> str:
    perm = required_perm(cmd)
    if perm is None:
        return "الجميع"
    if perm == "admin":
        return "مسؤول السيرفر فقط"
    return PERMISSION_CATEGORIES[perm][0]


def kind_of(cmd: app_commands.Command) -> str:
    perm = required_perm(cmd)
    if perm is not None:
        return "admin"
    return "games" if cmd.qualified_name.split()[0] == "game" else "general"


def shortcut_support(cmd: app_commands.Command) -> tuple[bool, str]:
    """هل يمكن تحويل هذا الأمر لاختصار نصي بأمان؟ (يرجع السبب عند الرفض)"""
    root = cmd.qualified_name.split()[0]
    if root in NO_SHORTCUT_ROOTS:
        return False, "أوامر إدارة البوت والموسيقى لا تُحوَّل لاختصارات نصية لأنها تحتاج واجهة ديسكورد التفاعلية"
    for p in cmd.parameters:
        if p.type == discord.AppCommandOptionType.attachment:
            return False, "الأمر يتطلب مرفقًا"
        raw = cmd._params.get(p.name)
        if raw is not None and raw.is_choice_annotation():
            return False, "الأمر يستخدم خيارات لا يمكن تمثيلها نصيًا"
    return True, ""


def set_option_description(cmd: app_commands.Command, option: str, text: str) -> bool:
    """يعدّل وصف خيار على الكائن الداخلي (واجهة Parameter العامة للقراءة فقط في discord.py)."""
    target = cmd._params.get(option)
    if target is None:
        return False
    target.description = text
    return True


def capture_defaults(tree: app_commands.CommandTree) -> None:
    """يلتقط الأوصاف الأصلية مرة واحدة ليتمكن المسؤول من الرجوع إليها."""
    for cmd in leaves(tree):
        _DEFAULTS.setdefault((cmd.qualified_name, None), str(cmd.description))
        for p in cmd.parameters:
            _DEFAULTS.setdefault((cmd.qualified_name, p.name), str(p.description))


def default_description(qualified: str, option: Optional[str] = None) -> str:
    return _DEFAULTS.get((qualified, option), "")


def apply_docs(tree: app_commands.CommandTree) -> int:
    """يطبّق الأوصاف الواضحة من الجداول أعلاه على كل أمر/خيار (يملأ الناقص ويحسّن الغامض)."""
    n = 0
    for cmd in leaves(tree):
        doc = COMMAND_DOCS.get(cmd.qualified_name)
        if doc:
            cmd.description = doc[:DISCORD_DESC_MAX]
            n += 1
        for p in cmd.parameters:
            text = OPTION_DOCS.get((cmd.qualified_name, p.name))
            if text and set_option_description(cmd, p.name, text[:DISCORD_DESC_MAX]):
                n += 1
    capture_defaults(tree)
    return n


def apply_overrides(tree: app_commands.CommandTree, configs: dict[str, dict]) -> int:
    """يطبّق أوصاف المسؤول المخصصة (وإلا يعيد الأصلي) على كائنات الأوامر في الذاكرة."""
    changed = 0
    for cmd in leaves(tree):
        cfg = configs.get(cmd.qualified_name, {})
        want = (cfg.get("description") or default_description(cmd.qualified_name))[:DISCORD_DESC_MAX]
        if want and str(cmd.description) != want:
            cmd.description = want
            changed += 1
        for p in cmd.parameters:
            want = (cfg.get("options", {}).get(p.name) or default_description(cmd.qualified_name, p.name))
            want = want[:DISCORD_DESC_MAX]
            if want and str(p.description) != want and set_option_description(cmd, p.name, want):
                changed += 1
    return changed


def validate_description(text: str) -> str:
    text = " ".join(text.split())
    if not 1 <= len(text) <= DISCORD_DESC_MAX:
        raise ValueError(f"الوصف يجب أن يكون بين 1 و {DISCORD_DESC_MAX} حرف (الحالي {len(text)}).")
    if any(ord(c) < 32 for c in text):
        raise ValueError("الوصف يحتوي محارف غير مسموحة.")
    return text


async def info_for(bot, guild_id: int, cmd: app_commands.Command) -> CommandInfo:
    configs = await bot.db.command_configs(guild_id)
    shortcuts = await bot.db.shortcuts(guild_id)
    cfg = configs.get(cmd.qualified_name, {})
    ok, reason = shortcut_support(cmd)
    opts = [OptionInfo(p.name, str(p.description), p.required,
                       OPTION_TYPE_LABELS.get(p.type, str(p.type.name)), [str(c.name) for c in p.choices][:10])
            for p in cmd.parameters]
    perm = required_perm(cmd)
    return CommandInfo(
        qualified=cmd.qualified_name, description=str(cmd.description),
        default_description=default_description(cmd.qualified_name) or str(cmd.description),
        kind=kind_of(cmd), perm=perm, perm_label=perm_label(cmd), options=opts,
        enabled=cfg.get("enabled", True), protected=cmd.qualified_name.split()[0] in PROTECTED,
        shortcuts=sorted(s for s, target in shortcuts.items() if target == cmd.qualified_name),
        shortcut_ok=ok, shortcut_reason=reason, group=cmd.qualified_name.split()[0] if cmd.parent else "")


async def is_disabled(bot, guild_id: Optional[int], qualified: str) -> bool:
    if guild_id is None or qualified.split()[0] in PROTECTED:
        return False
    cfg = (await bot.db.command_configs(guild_id)).get(qualified)
    return bool(cfg) and not cfg["enabled"]


# ---------------------------------------------------------------------- المزامنة مع ديسكورد
SYNC_COOLDOWN = 60.0
_last_sync = 0.0


def sync_wait() -> float:
    return max(0.0, SYNC_COOLDOWN - (time.monotonic() - _last_sync))


async def sync_to_discord(bot, guild_id: Optional[int]) -> int:
    """يعيد نسخ الأوامر (بأوصافها الحالية) إلى السيرفر ويزامنها. يُستدعى بطلب المسؤول فقط ومع مهلة انتظار."""
    global _last_sync
    _last_sync = time.monotonic()
    if guild_id:
        guild = discord.Object(id=guild_id)
        bot.tree.clear_commands(guild=guild)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
    else:
        synced = await bot.tree.sync()
    log.info("Command sync requested by admin: %d commands", len(synced))
    return len(synced)

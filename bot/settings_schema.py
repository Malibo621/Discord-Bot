"""تعريف كل الإعدادات القابلة للتعديل من الإدارة مع قيمها الافتراضية وحدودها."""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str  # int | bool | str | channel
    default: Any
    group: str
    min: Optional[int] = None
    max: Optional[int] = None


SETTINGS: list[Setting] = [
    # العملة
    Setting("currency_name", "اسم العملة", "str", "نقطة", "عام"),
    Setting("currency_emoji", "رمز العملة", "str", "🪙", "عام"),

    # القنوات
    Setting("log_channel", "روم سجل السيرفر", "channel", 0, "القنوات"),
    Setting("mod_log_channel", "روم سجل الإدارة", "channel", 0, "القنوات"),
    Setting("economy_log_channel", "روم سجل الاقتصاد", "channel", 0, "القنوات"),
    Setting("games_channel", "روم الألعاب (فارغ = كل الرومات)", "channel", 0, "القنوات"),

    # الألعاب: تفعيل/تعطيل ومدد كل لعبة
    Setting("game_roulette_enabled", "تفعيل لعبة الروليت", "bool", True, "الألعاب"),
    Setting("game_typing_enabled", "تفعيل لعبة أسرع كتابة", "bool", True, "الألعاب"),
    Setting("game_quiz_enabled", "تفعيل لعبة السؤال السريع", "bool", True, "الألعاب"),
    Setting("game_guess_enabled", "تفعيل لعبة خمّن الرقم", "bool", True, "الألعاب"),
    Setting("game_scramble_enabled", "تفعيل لعبة فكّك الكلمة", "bool", True, "الألعاب"),
    Setting("game_math_enabled", "تفعيل لعبة الحساب السريع", "bool", True, "الألعاب"),
    Setting("roulette_join_seconds", "مدة التسجيل في الروليت (ثانية)", "int", 40, "الألعاب", 15, 180),
    Setting("typing_seconds", "مهلة أسرع كتابة (ثانية)", "int", 20, "الألعاب", 8, 120),
    Setting("quiz_seconds", "مهلة السؤال السريع (ثانية)", "int", 20, "الألعاب", 8, 120),
    Setting("guess_seconds", "مهلة خمّن الرقم (ثانية)", "int", 60, "الألعاب", 20, 300),
    Setting("guess_max_tries", "محاولات كل عضو في خمّن الرقم", "int", 5, "الألعاب", 1, 30),
    Setting("scramble_seconds", "مهلة فكّك الكلمة (ثانية)", "int", 30, "الألعاب", 10, 120),
    Setting("math_seconds", "مهلة الحساب السريع (ثانية)", "int", 15, "الألعاب", 5, 120),

    # مكافآت الألعاب
    Setting("reward_roulette", "مكافأة الروليت", "int", 60, "مكافآت الألعاب", 0, 100000),
    Setting("reward_typing", "مكافأة أسرع كتابة", "int", 15, "مكافآت الألعاب", 0, 100000),
    Setting("reward_quiz", "مكافأة الأسئلة", "int", 20, "مكافآت الألعاب", 0, 100000),
    Setting("reward_guess", "مكافأة خمّن الرقم", "int", 25, "مكافآت الألعاب", 0, 100000),
    Setting("reward_scramble", "مكافأة فكّك الكلمة", "int", 20, "مكافآت الألعاب", 0, 100000),
    Setting("reward_math", "مكافأة الحساب السريع", "int", 15, "مكافآت الألعاب", 0, 100000),
    Setting("max_event_reward", "أقصى مكافأة لفعالية يحددها المنظّم", "int", 1000, "مكافآت الألعاب", 0, 1000000),

    # الحماية من الاستغلال
    Setting("games_public", "السماح للجميع ببدء الألعاب", "bool", True, "مكافحة الاستغلال"),
    Setting("game_cooldown", "انتظار بين الألعاب في نفس الروم (ثانية)", "int", 30, "مكافحة الاستغلال", 0, 3600),
    Setting("daily_earn_cap", "الحد اليومي لكسب النقاط من الألعاب", "int", 500, "مكافحة الاستغلال", 0, 10000000),
    Setting("min_account_age_days", "أقل عمر لحساب ديسكورد للكسب (يوم)", "int", 7, "مكافحة الاستغلال", 0, 3650),
    Setting("min_member_age_hours", "أقل مدة داخل السيرفر للكسب (ساعة)", "int", 24, "مكافحة الاستغلال", 0, 8760),
    Setting("roulette_min_players", "أقل عدد لاعبين لمكافأة الروليت", "int", 3, "مكافحة الاستغلال", 2, 50),

    # التحويلات
    Setting("transfer_tax", "ضريبة التحويل (%)", "int", 5, "التحويلات", 0, 50),
    Setting("transfer_min", "أقل مبلغ للتحويل", "int", 10, "التحويلات", 1, 1000000),
    Setting("transfer_daily_limit", "الحد اليومي للتحويل", "int", 1000, "التحويلات", 0, 100000000),
    Setting("transfer_cooldown", "انتظار بين التحويلات (ثانية)", "int", 60, "التحويلات", 0, 86400),

    # الحماية
    Setting("antispam_enabled", "مكافحة السبام", "bool", True, "الحماية"),
    Setting("antispam_messages", "عدد الرسائل المسموح", "int", 6, "الحماية", 2, 50),
    Setting("antispam_seconds", "خلال (ثانية)", "int", 8, "الحماية", 2, 120),
    Setting("antispam_timeout_minutes", "مدة كتم السبام (دقيقة)", "int", 10, "الحماية", 1, 40320),
    Setting("antilink_enabled", "منع الروابط", "bool", True, "الحماية"),
    Setting("antilink_block_invites", "منع دعوات ديسكورد", "bool", True, "الحماية"),
    Setting("allowed_domains", "النطاقات المسموحة (مفصولة بفاصلة)", "str",
            "tenor.com,giphy.com,youtube.com,youtu.be", "الحماية"),
    Setting("mass_mention_limit", "أقصى منشن في رسالة", "int", 5, "الحماية", 1, 50),
    Setting("antinuke_enabled", "الحماية من التخريب (حذف/حظر جماعي)", "bool", True, "الحماية"),
    Setting("antinuke_threshold", "عدد الإجراءات التخريبية المسموح خلال دقيقة", "int", 3, "الحماية", 1, 50),
    Setting("raid_join_threshold", "تنبيه دخول جماعي: عدد الأعضاء خلال 10 ثوانٍ", "int", 8, "الحماية", 2, 200),

    # التحذيرات
    Setting("warn_timeout_threshold", "عدد التحذيرات للكتم التلقائي (0 = تعطيل)", "int", 3, "الإدارة", 0, 50),
    Setting("warn_timeout_minutes", "مدة الكتم التلقائي (دقيقة)", "int", 60, "الإدارة", 1, 40320),
    Setting("warn_kick_threshold", "عدد التحذيرات للطرد التلقائي (0 = تعطيل)", "int", 0, "الإدارة", 0, 50),
]

SETTINGS_BY_KEY = {s.key: s for s in SETTINGS}

TRUE_WORDS = ("1", "true", "on", "yes", "نعم", "تفعيل", "مفعل", "مفعّل")
FALSE_WORDS = ("0", "false", "off", "no", "لا", "تعطيل", "معطل", "معطّل")


def format_value(setting: Setting, value: Any) -> str:
    if setting.kind == "bool":
        return "🟢 مفعّل" if value else "🔴 معطّل"
    if setting.kind == "channel":
        return f"<#{value}>" if value else "`غير محدد`"
    if setting.kind == "int":
        return f"`{value:,}`"
    return f"`{value}`"


def parse_value(setting: Setting, raw: str, channel_exists=None) -> Any:
    """يحوّل نص المسؤول لقيمة صالحة أو يرفع ValueError برسالة توضح السبب."""
    value = raw.strip()
    if setting.kind == "bool":
        if value.lower() in TRUE_WORDS:
            return True
        if value.lower() in FALSE_WORDS:
            return False
        raise ValueError("اكتب **نعم** أو **لا**.")
    if setting.kind == "int":
        try:
            n = int(value.replace(",", "").replace("٬", "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
        except ValueError:
            raise ValueError("القيمة يجب أن تكون رقمًا صحيحًا.") from None
        if (setting.min is not None and n < setting.min) or (setting.max is not None and n > setting.max):
            raise ValueError(f"القيمة يجب أن تكون بين **{setting.min:,}** و **{setting.max:,}**.")
        return n
    if setting.kind == "channel":
        digits = "".join(c for c in value if c.isdigit())
        if not digits or (channel_exists and not channel_exists(int(digits))):
            raise ValueError("اختر الروم من القائمة.")
        return int(digits)
    if not value or len(value) > 200:
        raise ValueError("النص يجب أن يكون بين 1 و 200 حرف.")
    if setting.key == "currency_emoji" and len(value) > 64:
        raise ValueError("رمز العملة طويل جدًا.")
    return value

# فئات الصلاحيات: الفئة -> (الوصف، صلاحية ديسكورد الافتراضية إن لم تُحدَّد رتب)
PERMISSION_CATEGORIES = {
    "moderation": ("أوامر الإدارة (تحذير/كتم/طرد/حظر/مسح)", "moderate_members"),
    "economy": ("التحكم بالنقاط (إضافة/خصم/تصفير)", "manage_guild"),
    "shop": ("إدارة المتجر والطلبات", "manage_guild"),
    "events": ("بدء الفعاليات بمكافآت مخصصة", "manage_events"),
    "music": ("التحكم الكامل بالموسيقى (تخطي/إيقاف للجميع)", "move_members"),
    "bypass": ("تجاوز أنظمة الحماية (سبام/روابط)", "manage_messages"),
}

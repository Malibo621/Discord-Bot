"""أدوات مساعدة: تطبيع النص العربي، تحويل المدد، التحقق من أهلية الكسب."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿­]")


def has_invisible(text: str) -> bool:
    return bool(_INVISIBLE.search(text))


def normalize(text: str) -> str:
    """توحيد الكتابة العربية للمقارنة: الهمزات، التاء المربوطة، الألف المقصورة، التشكيل."""
    text = _INVISIBLE.sub("", text)
    text = _DIACRITICS.sub("", text)
    text = re.sub("[إأآٱ]", "ا", text)
    text = text.replace("ة", "ه").replace("ى", "ي").replace("ؤ", "و").replace("ئ", "ي")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def obfuscate(word: str) -> str:
    """يدرج محارف غير مرئية بين الحروف لمنع النسخ واللصق في ألعاب الكتابة."""
    return "​".join(word)


_DURATION = re.compile(r"(\d+)\s*([smhdwثدسيأ]?)", re.IGNORECASE)
_UNITS = {
    "s": 1, "ث": 1,
    "m": 60, "د": 60, "": 60,
    "h": 3600, "س": 3600,
    "d": 86400, "ي": 86400,
    "w": 604800, "أ": 604800,
}


def parse_duration(text: str) -> Optional[timedelta]:
    """يقبل: 10m / 2h / 1d / 1w أو بالعربي 10د / 2س / 1ي. الرقم وحده = دقائق."""
    text = text.strip().lower().replace(" ", "")
    if not text:
        return None
    total = 0
    pos = 0
    for m in _DURATION.finditer(text):
        if m.start() != pos:
            return None
        total += int(m.group(1)) * _UNITS[m.group(2)]
        pos = m.end()
    if pos != len(text) or total <= 0:
        return None
    return timedelta(seconds=total)


def human_duration(td: timedelta) -> str:
    seconds = int(td.total_seconds())
    parts = []
    for unit, label in ((86400, "يوم"), (3600, "ساعة"), (60, "دقيقة"), (1, "ثانية")):
        if seconds >= unit:
            n, seconds = divmod(seconds, unit)
            parts.append(f"{n} {label}")
    return " و ".join(parts) or "0 ثانية"


def ts(dt: datetime | int, style: str = "R") -> str:
    if isinstance(dt, datetime):
        dt = int(dt.timestamp())
    return f"<t:{dt}:{style}>"


def earning_block_reason(member: discord.Member, settings: dict) -> Optional[str]:
    """يرجع سبب منع العضو من كسب النقاط، أو None إن كان مؤهلًا."""
    if member.bot:
        return "البوتات لا تكسب نقاطًا."
    now = datetime.now(timezone.utc)
    min_acc = settings["min_account_age_days"]
    if min_acc and now - member.created_at < timedelta(days=min_acc):
        return f"حسابك أحدث من {min_acc} يوم، لذلك لا تُحتسب لك النقاط بعد."
    min_mem = settings["min_member_age_hours"]
    if min_mem and member.joined_at and now - member.joined_at < timedelta(hours=min_mem):
        return f"يجب أن تمضي {min_mem} ساعة في السيرفر قبل أن تكسب النقاط."
    return None


def can_act_on(actor: discord.Member, target: discord.Member) -> Optional[str]:
    """التحقق من التسلسل الهرمي للرتب قبل أي إجراء إداري."""
    guild = actor.guild
    if target.id == actor.id:
        return "لا يمكنك تنفيذ هذا الإجراء على نفسك."
    if target.id == guild.owner_id:
        return "لا يمكن تنفيذ هذا الإجراء على مالك السيرفر."
    if target.id == guild.me.id:
        return "لا يمكنني تنفيذ هذا الإجراء على نفسي."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "رتبة العضو أعلى من رتبتك أو مساوية لها."
    if target.top_role >= guild.me.top_role:
        return "رتبة العضو أعلى من رتبتي، ارفع رتبة البوت أولًا."
    return None

"""روم الاتصال الدائم (24/7): تحديد القناة وحساب فترات إعادة المحاولة.

القناة تُحدَّد بوضوح من مكانين فقط (ولا يدخل البوت أي روم آخر من تلقائه):
1) إعداد السيرفر «voice_home_channel» (من /panel ← القنوات أو /settings channel)
2) متغير البيئة VOICE_CHANNEL_ID في ملف .env (يُستخدم إن لم يوجد إعداد محفوظ)
"""
from __future__ import annotations

import os
from typing import Optional

import discord

# فترات الانتظار بين محاولات إعادة الاتصال (ثوانٍ): تتصاعد ثم تثبت عند 5 دقائق لتجنب حلقات إعادة الاتصال
BACKOFF = (5, 15, 30, 60, 120, 300)


def backoff_for(failures: int) -> int:
    return BACKOFF[min(max(failures, 1) - 1, len(BACKOFF) - 1)]


async def home_channel(bot, guild: discord.Guild) -> Optional[discord.VoiceChannel | discord.StageChannel]:
    cid = (await bot.db.get_settings(guild.id))["voice_home_channel"]
    if not cid:
        env = os.getenv("VOICE_CHANNEL_ID", "").strip()
        cid = int(env) if env.isdigit() else 0
    ch = guild.get_channel(cid) if cid else None
    return ch if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)) else None


def missing_permissions(channel: discord.abc.GuildChannel) -> list[str]:
    perms = channel.permissions_for(channel.guild.me)
    need = {"view_channel": "رؤية الروم", "connect": "الاتصال", "speak": "التحدث"}
    return [label for key, label in need.items() if not getattr(perms, key)]

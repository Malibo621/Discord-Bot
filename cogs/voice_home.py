"""اتصال دائم بروم صوتي محدد: ينضم عند التشغيل، ويعيد الاتصال بأمان عند الانقطاع.

لا يعتمد على تشغيل أغنية. لا يدخل إلا الروم المحدد في الإعداد/المتغير، ويعود إليه إن نُقل أو طُرد.
إعادة المحاولة تتصاعد (5ث ← 15ث ← 30ث ← 1د ← 2د ← 5د) وتُصفَّر عند النجاح، فلا تنشأ حلقة اتصال متكررة.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import discord
from discord.ext import commands, tasks

from bot import voice_home

log = logging.getLogger("bot.voice")
WATCHDOG_SECONDS = 20


@dataclass
class _State:
    failures: int = 0
    next_try: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_warn: float = 0.0
    was_connected: bool = False


class VoiceHome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.state: dict[int, _State] = {}

    async def cog_load(self):
        self.watchdog.start()

    async def cog_unload(self):
        self.watchdog.cancel()

    def _st(self, guild_id: int) -> _State:
        return self.state.setdefault(guild_id, _State())

    # ------------------------------------------------------------------ المراقبة
    @tasks.loop(seconds=WATCHDOG_SECONDS)
    async def watchdog(self):
        for guild in list(self.bot.guilds):
            try:
                await self.ensure(guild)
            except Exception:  # noqa: BLE001 - أي خطأ لا يجوز أن يوقف المراقبة نهائيًا
                log.exception("Voice watchdog error in guild %s", guild.id)

    @watchdog.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    async def ensure(self, guild: discord.Guild) -> None:
        channel = await voice_home.home_channel(self.bot, guild)
        st = self._st(guild.id)
        if channel is None:
            st.failures, st.next_try = 0, 0.0
            return
        vc = guild.voice_client
        if vc is not None and vc.is_connected() and vc.channel and vc.channel.id == channel.id:
            if not st.was_connected:
                log.info("Voice home connected: #%s (guild %s)", channel.name, guild.id)
            st.failures, st.next_try, st.was_connected = 0, 0.0, True
            return
        if time.monotonic() < st.next_try:
            return
        missing = voice_home.missing_permissions(channel)
        if missing:
            self._failed(st, guild, f"البوت يفتقد صلاحيات في #{channel.name}: {', '.join(missing)}", quiet_after=600)
            return
        if st.lock.locked():
            return
        async with st.lock:
            try:
                if vc is not None and vc.is_connected():
                    log.info("Voice home: moving back to #%s (was in #%s)", channel.name, vc.channel.name)
                    await vc.move_to(channel)
                else:
                    if vc is not None:   # اتصال ميت: نظّفه قبل الاتصال من جديد
                        await vc.disconnect(force=True)
                    log.info("Voice home: connecting to #%s (guild %s, attempt %d)", channel.name, guild.id,
                             st.failures + 1)
                    await channel.connect(self_deaf=True, timeout=20.0, reconnect=True)
            except discord.ClientException:   # «Already connected»: الحالة سليمة
                pass
            except asyncio.TimeoutError:
                self._failed(st, guild, "انتهت مهلة الاتصال بالروم الصوتي")
                return
            except discord.Forbidden:
                self._failed(st, guild, "ديسكورد رفض الاتصال (صلاحيات)")
                return
            except Exception as exc:  # noqa: BLE001
                log.exception("Voice home connect failed")
                self._failed(st, guild, f"فشل الاتصال: {type(exc).__name__}")
                return
        st.failures, st.next_try, st.was_connected = 0, 0.0, True
        log.info("Voice home ready in #%s", channel.name)

    def _failed(self, st: _State, guild: discord.Guild, reason: str, quiet_after: float = 0.0) -> None:
        st.failures += 1
        wait = voice_home.backoff_for(st.failures)
        st.next_try = time.monotonic() + wait
        now = time.monotonic()
        if now - st.last_warn >= quiet_after:
            log.warning("Voice home: %s — retry in %ds (failure #%d, guild %s)", reason, wait, st.failures, guild.id)
            st.last_warn = now

    # ------------------------------------------------------------------ أحداث الاتصال
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if not self.bot.user or member.id != self.bot.user.id:
            return
        st = self._st(member.guild.id)
        home = await voice_home.home_channel(self.bot, member.guild)
        if home is None:
            return
        if before.channel and after.channel is None:
            st.was_connected = False
            st.next_try = min(st.next_try, time.monotonic() + 3) if st.failures == 0 else st.next_try
            log.warning("Voice home: disconnected from #%s (kicked/network). Reconnecting soon.", before.channel.name)
        elif after.channel and after.channel.id != home.id:
            st.next_try = time.monotonic() + 5
            log.warning("Voice home: bot was moved to #%s; returning to #%s.", after.channel.name, home.name)

    @commands.Cog.listener()
    async def on_resumed(self):
        log.info("Gateway session resumed; voice watchdog will verify the voice connection.")


async def setup(bot):
    await bot.add_cog(VoiceHome(bot))

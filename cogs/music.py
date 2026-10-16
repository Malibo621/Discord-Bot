"""نظام الموسيقى: تشغيل من يوتيوب/ساوند كلاود عبر yt-dlp مع قائمة انتظار لكل سيرفر."""
from __future__ import annotations

import asyncio
import logging
import random
import shutil
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from bot import cards, images, ui, voice_home
from bot.checks import has_category

log = logging.getLogger("bot.music")

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "extract_flat": False,
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)
IDLE_TIMEOUT = 180  # ثوانٍ قبل الخروج التلقائي عند الفراغ


@dataclass
class Track:
    title: str
    url: str          # رابط الصفحة
    stream: str       # رابط البث
    duration: int
    thumbnail: Optional[str]
    requester: discord.Member
    fetched_at: float = 0.0

    @property
    def length(self) -> str:
        if not self.duration:
            return "مباشر"
        m, s = divmod(self.duration, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def explain_error(exc: BaseException) -> str:
    """رسالة مفهومة للمستخدم بدل الفشل الصامت (التفاصيل الكاملة تُسجَّل في الـ log)."""
    text = str(exc).lower()
    if isinstance(exc, LookupError):
        return "لم أجد نتيجة لهذا البحث. جرّب اسمًا أوضح أو أضف اسم الفنان."
    if isinstance(exc, asyncio.TimeoutError):
        return "استغرق البحث وقتًا طويلًا. حاول مرة أخرى بعد قليل."
    if "private video" in text or "members-only" in text:
        return "هذا المقطع خاص أو للأعضاء فقط."
    if "sign in" in text or "confirm your age" in text or "age-restricted" in text or "not a bot" in text:
        return "يوتيوب يطلب تسجيل دخول أو تحققًا عمريًا لهذا المقطع. جرّب مقطعًا آخر."
    if "unavailable" in text or "not available" in text or "removed" in text or "blocked" in text:
        return "هذا المقطع غير متاح (محذوف أو محجوب في منطقة البوت). جرّب غيره."
    if "unsupported url" in text:
        return "الرابط غير مدعوم. استخدم اسم الأغنية أو رابط يوتيوب/ساوند كلاود."
    if any(k in text for k in ("urlopen error", "timed out", "connection", "getaddrinfo", "network")):
        return "تعذّر الاتصال بمصدر الأغاني (شبكة). حاول بعد قليل."
    return "تعذّر جلب الأغنية. جرّب اسمًا أو رابطًا آخر."


async def fetch_track(query: str, requester: discord.Member) -> Track:
    loop = asyncio.get_running_loop()
    last: Optional[BaseException] = None
    data = None
    for attempt in (1, 2):   # محاولة ثانية للأعطال العابرة فقط
        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False)), timeout=45)
            break
        except yt_dlp.utils.DownloadError as exc:
            last = exc
            if "unavailable" in str(exc).lower() or "private" in str(exc).lower() or "sign in" in str(exc).lower():
                break
        except (asyncio.TimeoutError, OSError) as exc:
            last = exc
    if data is None:
        raise last or LookupError("no results")
    if "entries" in data:
        entries = [e for e in data["entries"] if e]
        if not entries:
            raise LookupError("no results")
        data = entries[0]
    return Track(
        title=data.get("title", "بدون عنوان"),
        url=data.get("webpage_url", query),
        stream=data["url"],
        duration=int(data.get("duration") or 0),
        thumbnail=data.get("thumbnail"),
        requester=requester,
        fetched_at=time.monotonic(),
    )


class GuildPlayer:
    def __init__(self, cog: "Music", guild: discord.Guild):
        self.cog = cog
        self.guild = guild
        self.queue: deque[Track] = deque()
        self.current: Optional[Track] = None
        self.volume = 0.5
        self.loop = False
        self.text_channel: Optional[discord.abc.Messageable] = None
        self._next = asyncio.Event()
        self.task = asyncio.create_task(self._runner())

    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client  # type: ignore[return-value]

    async def _runner(self):
        while True:
            self._next.clear()
            if self.loop and self.current:
                track = self.current
            else:
                try:
                    track = await asyncio.wait_for(self._wait_for_track(), timeout=IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    return await self.destroy("⏹️ غادرت الروم الصوتي لعدم وجود أغانٍ.")
            self.current = track
            vc = self.voice
            if not vc:
                return await self.destroy()
            try:
                # روابط البث تنتهي صلاحيتها، لذلك نجددها إن كانت قديمة
                if time.monotonic() - track.fetched_at > 300:
                    fresh = await fetch_track(track.url, track.requester)
                    track.stream, track.fetched_at = fresh.stream, fresh.fetched_at
                source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(track.stream, **FFMPEG_OPTS),
                                                      volume=self.volume)
            except Exception as exc:  # noqa: BLE001
                log.exception("Could not start playback of %r", track.title)
                if self.text_channel:
                    await ui.safe_send(self.text_channel, embed=ui.error(
                        f"تعذّر تشغيل **{track.title}** وتم التخطي.\n{explain_error(exc)}"))
                self.current = None
                continue
            vc.play(source, after=lambda _e: self.cog.bot.loop.call_soon_threadsafe(self._next.set))
            if self.text_channel and not self.loop:
                await ui.safe_send(self.text_channel, **await self.now_playing_message())
            await self._next.wait()
            if not self.loop:
                self.current = None

    async def _wait_for_track(self) -> Track:
        while not self.queue:
            await asyncio.sleep(1)
        return self.queue.popleft()

    async def now_playing_message(self) -> dict:
        """بطاقة «يعمل الآن» المصوّرة (غلاف الأغنية والمدة والصوت) وبديلها النصي عند أي فشل."""
        t = self.current
        s = await self.cog.bot.db.get_settings(self.guild.id)
        if s["info_cards"] and cards.available():
            try:
                thumb = await images.fetch(t.thumbnail) if t.thumbnail else None
                png = await asyncio.to_thread(
                    cards.render_nowplaying, title=t.title, requester=t.requester.display_name, duration=t.length,
                    thumbnail=thumb, queue_len=len(self.queue), volume=int(self.volume * 100), loop=self.loop,
                    guild_name=self.guild.name)
                kwargs: dict = {"file": ui.card_file(png, "nowplaying.png", f"يعمل الآن: {t.title}")}
                e = ui.info(f"{ui.Icons.MUSIC} {t.title}"[:250], f"[رابط الأغنية]({t.url})", ui.Colors.MUSIC)
                if s["game_image_in_embed"]:
                    e.set_image(url="attachment://nowplaying.png")
                kwargs["embed"] = e
                return kwargs
            except Exception:  # noqa: BLE001
                log.exception("Now-playing card failed; using text embed")
        return {"embed": self.now_playing_embed()}

    def now_playing_embed(self) -> discord.Embed:
        t = self.current
        e = ui.info(f"{ui.Icons.MUSIC} يتم التشغيل الآن", f"**[{t.title}]({t.url})**", ui.Colors.MUSIC)
        e.add_field(name="⏱️ المدة", value=t.length)
        e.add_field(name="🙋 بطلب من", value=t.requester.mention)
        e.add_field(name="🔊 الصوت", value=f"{int(self.volume * 100)}%")
        if self.loop:
            e.add_field(name="🔁 التكرار", value="مفعّل")
        if self.queue:
            e.set_footer(text=f"التالي: {self.queue[0].title[:80]} • في القائمة {len(self.queue)}")
        if t.thumbnail:
            e.set_thumbnail(url=t.thumbnail)
        return e

    async def destroy(self, message: Optional[str] = None):
        self.queue.clear()
        self.current = None
        if self.voice and await voice_home.home_channel(self.cog.bot, self.guild) is None:
            await self.voice.disconnect(force=True)   # مع الاتصال الدائم يبقى البوت في روم المنزل
        if message and self.text_channel:
            try:
                await self.text_channel.send(embed=ui.info(message, color=ui.Colors.LOG))
            except discord.HTTPException:
                pass
        self.cog.players.pop(self.guild.id, None)
        if self.task is not asyncio.current_task():
            self.task.cancel()


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    music = app_commands.Group(name="music", description="أوامر الموسيقى", guild_only=True)

    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(self, guild)
        return self.players[guild.id]

    async def _same_channel(self, interaction: discord.Interaction) -> bool:
        vc = interaction.guild.voice_client
        user_vc = interaction.user.voice.channel if interaction.user.voice else None
        if not vc:
            await ui.reply(interaction, ui.error("البوت غير متصل بأي روم صوتي."), ephemeral=True)
            return False
        if user_vc != vc.channel:
            await ui.reply(interaction, ui.error("يجب أن تكون في نفس الروم الصوتي مع البوت."), ephemeral=True)
            return False
        return True

    async def _can_control(self, interaction: discord.Interaction, player: GuildPlayer) -> bool:
        """صاحب الأغنية الحالية أو من لديه صلاحية الموسيقى أو إذا كان وحده مع البوت."""
        if await has_category(self.bot.db, interaction.user, "music"):
            return True
        if player.current and player.current.requester.id == interaction.user.id:
            return True
        humans = [m for m in interaction.guild.voice_client.channel.members if not m.bot]
        if len(humans) <= 1:
            return True
        await ui.reply(interaction, ui.error("هذا الإجراء لصاحب الأغنية أو لمن يملك صلاحية التحكم بالموسيقى."),
                       ephemeral=True)
        return False

    async def _play(self, interaction: discord.Interaction, query: str):
        home = await voice_home.home_channel(self.bot, interaction.guild)
        user_ch = interaction.user.voice.channel if interaction.user.voice else None
        if home is not None:
            if user_ch is None or user_ch.id != home.id:
                return await ui.reply(interaction, ui.error(
                    f"البوت مثبّت في {home.mention} (اتصال دائم). ادخل هذا الروم ثم اطلب الأغنية."), ephemeral=True)
            channel = home
        else:
            if user_ch is None:
                return await ui.reply(interaction, ui.error("ادخل روم صوتي أولًا."), ephemeral=True)
            channel = user_ch
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.connect and perms.speak):
            return await ui.reply(interaction, ui.error("لا أملك صلاحية الدخول أو التحدث في هذا الروم."),
                                  ephemeral=True)
        if shutil.which("ffmpeg") is None:
            log.error("FFmpeg not found in PATH: music cannot play")
            return await ui.reply(interaction, ui.error(
                "FFmpeg غير مثبّت أو غير موجود في PATH على جهاز تشغيل البوت. ثبّته ثم أعد تشغيل البوت.",
                "الموسيقى غير جاهزة"), ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.channel != channel:
            return await ui.reply(interaction, ui.error(f"البوت مشغول في {vc.channel.mention}."), ephemeral=True)

        await interaction.response.defer()
        try:
            track = await fetch_track(query, interaction.user)
        except Exception as exc:  # noqa: BLE001
            log.warning("Music search failed for %r: %s: %s", query, type(exc).__name__, str(exc)[:300])
            return await interaction.followup.send(embed=ui.error(explain_error(exc), "تعذّر العثور على الأغنية"))
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            try:
                if vc:
                    await vc.disconnect(force=True)
                await channel.connect(self_deaf=True, timeout=20.0, reconnect=True)
            except asyncio.TimeoutError:
                log.warning("Voice connect timed out in %s", channel.id)
                return await interaction.followup.send(embed=ui.error(
                    "انتهت مهلة الاتصال بالروم الصوتي. تأكد من صلاحيات البوت وحاول مجددًا.", "تعذّر الاتصال"))
            except (discord.ClientException, discord.HTTPException) as exc:
                log.exception("Voice connect failed")
                return await interaction.followup.send(embed=ui.error(
                    f"تعذّر الاتصال بالروم الصوتي ({type(exc).__name__}).", "تعذّر الاتصال"))
        player = self.get_player(interaction.guild)
        player.text_channel = interaction.channel
        if len(player.queue) >= 100:
            return await interaction.followup.send(embed=ui.error("قائمة الانتظار ممتلئة (100 أغنية)."))
        player.queue.append(track)
        if player.current:
            e = ui.info("➕ أُضيفت للقائمة", f"**[{track.title}]({track.url})**", ui.Colors.MUSIC)
            e.add_field(name="الترتيب", value=f"`#{len(player.queue)}`")
            e.add_field(name="المدة", value=track.length)
            if track.thumbnail:
                e.set_thumbnail(url=track.thumbnail)
            await interaction.followup.send(embed=e)
        else:
            await interaction.followup.send(embed=ui.info(f"🔎 جاري تشغيل **{track.title}**", color=ui.Colors.MUSIC))

    @music.command(name="play", description="تشغيل أغنية بالاسم أو الرابط")
    @app_commands.describe(query="اسم الأغنية أو رابط يوتيوب/ساوند كلاود")
    async def play(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 300]):
        await self._play(interaction, query)

    @app_commands.command(name="play", description="تشغيل أغنية بالاسم أو الرابط (اختصار لـ /music play)")
    @app_commands.describe(query="اسم الأغنية أو رابط يوتيوب/ساوند كلاود")
    @app_commands.guild_only()
    async def play_alias(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 300]):
        await self._play(interaction, query)

    @music.command(name="skip", description="تخطي الأغنية الحالية")
    async def skip(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not player.current:
            return await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)
        if not await self._can_control(interaction, player):
            return
        player.loop = False
        interaction.guild.voice_client.stop()
        await ui.reply(interaction, ui.info("⏭️ تم التخطي", color=ui.Colors.MUSIC))

    @music.command(name="pause", description="إيقاف مؤقت")
    async def pause(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        vc = interaction.guild.voice_client
        if vc.is_playing():
            vc.pause()
            return await ui.reply(interaction, ui.info("⏸️ إيقاف مؤقت", color=ui.Colors.MUSIC))
        await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)

    @music.command(name="resume", description="استئناف التشغيل")
    async def resume(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        vc = interaction.guild.voice_client
        if vc.is_paused():
            vc.resume()
            return await ui.reply(interaction, ui.info("▶️ تم الاستئناف", color=ui.Colors.MUSIC))
        await ui.reply(interaction, ui.error("التشغيل ليس متوقفًا."), ephemeral=True)

    @music.command(name="stop", description="إيقاف الموسيقى ومسح القائمة والخروج")
    async def stop(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        await ui.reply(interaction, ui.info("⏹️ تم الإيقاف والخروج", color=ui.Colors.MUSIC))
        await player.destroy()

    @music.command(name="queue", description="عرض قائمة الانتظار")
    async def queue(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or (not player.current and not player.queue):
            return await ui.reply(interaction, ui.info("📭 القائمة فارغة", color=ui.Colors.MUSIC), ephemeral=True)
        lines = [f"`{i}.` [{t.title[:60]}]({t.url}) • {t.length} • {t.requester.mention}"
                 for i, t in enumerate(list(player.queue)[:15], 1)]
        desc = f"**الآن:** [{player.current.title}]({player.current.url})\n\n" if player.current else ""
        desc += "\n".join(lines) or "لا يوجد شيء بعدها."
        e = ui.info(f"📃 قائمة الانتظار ({len(player.queue)})", desc, ui.Colors.MUSIC)
        total = sum(t.duration for t in player.queue)
        e.set_footer(text=f"المدة الإجمالية: {total // 60} دقيقة")
        await ui.reply(interaction, e)

    @music.command(name="nowplaying", description="الأغنية الحالية")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or not player.current:
            return await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)
        msg = await player.now_playing_message()
        await ui.reply(interaction, msg["embed"], file=msg.get("file"))

    @music.command(name="volume", description="تغيير مستوى الصوت")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 1, 150]):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        player.volume = percent / 100
        vc = interaction.guild.voice_client
        if vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = player.volume
        await ui.reply(interaction, ui.info(f"🔊 الصوت: {percent}%", color=ui.Colors.MUSIC))

    @music.command(name="loop", description="تكرار الأغنية الحالية")
    async def loop(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        player.loop = not player.loop
        await ui.reply(interaction, ui.info("🔁 التكرار مفعّل" if player.loop else "➡️ التكرار معطّل",
                                            color=ui.Colors.MUSIC))

    @music.command(name="shuffle", description="خلط قائمة الانتظار")
    async def shuffle(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        items = list(player.queue)
        random.shuffle(items)
        player.queue = deque(items)
        await ui.reply(interaction, ui.info("🔀 تم خلط القائمة", color=ui.Colors.MUSIC))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, _after):
        vc = member.guild.voice_client
        if not vc or not before.channel or before.channel != vc.channel:
            return
        if member.id == self.bot.user.id:
            return
        if await voice_home.home_channel(self.bot, member.guild) is not None:
            return   # اتصال دائم: لا يغادر عند خلو الروم
        if all(m.bot for m in vc.channel.members):
            await asyncio.sleep(60)
            vc = member.guild.voice_client
            if vc and all(m.bot for m in vc.channel.members):
                player = self.players.get(member.guild.id)
                if player:
                    await player.destroy("👋 غادرت لأن الروم الصوتي أصبح فارغًا.")
                else:
                    await vc.disconnect(force=True)

    async def cog_unload(self):
        for p in list(self.players.values()):
            await p.destroy()


async def setup(bot):
    await bot.add_cog(Music(bot))

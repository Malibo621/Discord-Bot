"""نظام الموسيقى: تشغيل عبر yt-dlp + FFmpeg، قائمة انتظار ولوحة تحكم تفاعلية مستقلة لكل سيرفر.

- لوحة التحكم (MusicPanelView): رسالة واحدة (Components V2) تُحدَّث في مكانها عند تغيّر الأغنية أو حالتها، بترتيب ثابت
  من الأعلى للأسفل: بطاقة الأغنية (غلاف/عنوان/فنان/تقدّم) ← أزرار التحكم ← الطابور القادم (قائمة رأسية).
  الطابور الكامل بصفحاته وإدارته (حذف/مسح) في رسالة مخفية بزر «القائمة الكاملة».
- الوقت المعروض هو عدّاد إطارات الصوت المُرسَلة فعلًا لديسكورد (20ms للإطار) فيتوقف مع الإيقاف المؤقت؛ ولا يُعرض
  تقدّم إن تعذّر قراءته أو كانت المدة غير معروفة (بث مباشر).
- الصلاحيات تُفحص من الخادم عند كل زر وأمر: الإيقاف المؤقت لمن هو في نفس الروم الصوتي، وبقية التحكم لصاحب الأغنية
  أو لمن يملك صلاحية «الموسيقى» أو إن كان وحده مع البوت؛ وإدارة القائمة (حذف/مسح) لصلاحية «الموسيقى» فقط.
- مع الاتصال الدائم (24/7) لا يغادر البوت الروم أبدًا عند انتهاء القائمة.
"""
from __future__ import annotations

import asyncio
import logging
import random
import shutil
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

from bot import cards, images, ui, voice_home
from bot.checks import has_category, require

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
IDLE_TIMEOUT = 180        # ثوانٍ قبل إنهاء الجلسة عند فراغ القائمة
MAX_QUEUE = 100
HISTORY_SIZE = 25
VOLUME_STEP = 10
MIN_VOLUME, MAX_VOLUME = 5, 150
PANEL_MIN_INTERVAL = 1.0  # أقل فاصل بين تحديثات اللوحة التلقائية (حماية من حدود ديسكورد)
QUEUE_PAGE = 10
QUEUE_INLINE = 5          # عدد الأغاني القادمة الظاهرة داخل اللوحة نفسها
RECONNECT_WAIT = 20       # ثوانٍ ننتظر فيها عودة الاتصال الصوتي (24/7) قبل التخلي عن الجلسة


@dataclass
class Track:
    title: str
    url: str          # رابط الصفحة
    stream: str       # رابط البث
    duration: int
    thumbnail: Optional[str]
    requester: discord.Member
    fetched_at: float = 0.0
    artist: Optional[str] = None
    by_channel: bool = False      # True = الاسم المعروض اسم قناة وليس فنانًا
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

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
    artist = data.get("artist") or data.get("creator")
    by_channel = False
    if not artist:
        artist, by_channel = data.get("uploader") or data.get("channel"), True
    return Track(
        title=data.get("title", "بدون عنوان"),
        url=data.get("webpage_url", query),
        stream=data["url"],
        duration=int(data.get("duration") or 0),
        thumbnail=data.get("thumbnail"),
        requester=requester,
        fetched_at=time.monotonic(),
        artist=artist,
        by_channel=by_channel,
    )


class GuildPlayer:
    """حالة التشغيل لسيرفر واحد: قائمة، تاريخ، صوت، ولوحة التحكم. لا تشترك فيها السيرفرات."""

    def __init__(self, cog: "Music", guild: discord.Guild):
        self.cog = cog
        self.guild = guild
        self._aio = asyncio.get_running_loop()
        self.queue: deque[Track] = deque()
        self.history: deque[Track] = deque(maxlen=HISTORY_SIZE)
        self.current: Optional[Track] = None
        self.volume = 0.5
        self.loop = False
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.panel_message: Optional[discord.Message] = None
        self.panel_view: Optional["MusicPanelView"] = None
        self.last_error: Optional[BaseException] = None
        self._have = asyncio.Event()
        self._ended = asyncio.Event()
        self._no_history = False
        self._last_panel = 0.0
        self._panel_task: Optional[asyncio.Task] = None
        self._panel_lock = asyncio.Lock()
        self.connect_lock = asyncio.Lock()
        self.task = asyncio.create_task(self._runner())

    # ------------------------------------------------------------------ الحالة
    @property
    def voice(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client  # type: ignore[return-value]

    @property
    def paused(self) -> bool:
        vc = self.voice
        return bool(vc and vc.is_paused())

    def elapsed(self) -> Optional[float]:
        """ثواني التشغيل الفعلية = إطارات الصوت المُرسَلة × 20ms (تتوقف مع الإيقاف المؤقت). None إن لم تتوفر."""
        vc = self.voice
        loops = getattr(getattr(vc, "_player", None), "loops", None)
        if self.current is None or not isinstance(loops, int):
            return None
        return loops * 0.02

    def enqueue(self, track: Track) -> Optional[int]:
        """يضيف لنهاية القائمة ويرجع ترتيبه، أو None إن امتلأت القائمة."""
        if len(self.queue) >= MAX_QUEUE:
            return None
        self.queue.append(track)
        self._have.set()
        self.request_panel()
        return len(self.queue)

    # ------------------------------------------------------------------ حلقة التشغيل
    def _after(self, error: Optional[Exception]) -> None:
        self._aio.call_soon_threadsafe(self._finished, error)

    def _finished(self, error: Optional[Exception]) -> None:
        if error is not None:
            self.last_error = error
            log.error("Playback ended with error: %r", error)
        self._ended.set()

    async def _next_track(self) -> Optional[Track]:
        if self.loop and self.current is not None:
            return self.current
        while not self.queue:
            self._have.clear()
            try:
                await asyncio.wait_for(self._have.wait(), timeout=IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                return None
        return self.queue.popleft()

    async def _wait_voice(self) -> Optional[discord.VoiceClient]:
        for _ in range(RECONNECT_WAIT // 2):
            vc = self.voice
            if vc and vc.is_connected():
                return vc
            await asyncio.sleep(2)
        return None

    async def _runner(self):
        try:
            while True:
                track = await self._next_track()
                if track is None:
                    return await self.destroy("⏹️ انتهت القائمة ولم تُطلب أغانٍ جديدة.", idle=True)
                self.current = track
                vc = self.voice if (self.voice and self.voice.is_connected()) else await self._wait_voice()
                if vc is None:
                    return await self.destroy("⏹️ انقطع الاتصال الصوتي وتوقفت القائمة.", idle=True)
                try:
                    # روابط البث تنتهي صلاحيتها، لذلك نجددها إن كانت قديمة
                    if time.monotonic() - track.fetched_at > 300:
                        fresh = await fetch_track(track.url, track.requester)
                        track.stream, track.fetched_at = fresh.stream, fresh.fetched_at
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(track.stream, **FFMPEG_OPTS),
                                                          volume=self.volume)
                    self._ended.clear()
                    self.last_error = None
                    vc.play(source, after=self._after)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Could not start playback of %r", track.title)
                    self.loop = False       # لا حلقة فشل متكررة على نفس الأغنية
                    self.current = None
                    if self.text_channel:
                        await ui.safe_send(self.text_channel, embed=ui.error(
                            f"تعذّر تشغيل **{track.title}** وتم التخطي.\n{explain_error(exc)}"))
                    continue
                await self.refresh_panel(force=True)
                await self._ended.wait()
                if self.last_error is not None and self.text_channel:
                    await ui.safe_send(self.text_channel, embed=ui.error(
                        f"توقف تشغيل **{track.title}** بشكل غير متوقع، ننتقل للتالية.", "تعطل التشغيل"))
                if not self.loop:
                    if self.current is not None and not self._no_history:
                        self.history.append(self.current)
                    self.current = None
                self._no_history = False
                await self.refresh_panel(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - انهيار غير متوقع: سجّله وأنهِ الجلسة بنظافة
            log.exception("Player runner crashed (guild %s)", self.guild.id)
            await self.destroy("⚠️ حدث خطأ غير متوقع في المشغّل وتوقفت الجلسة.", idle=True)

    # ------------------------------------------------------------------ التحكم
    def skip(self) -> bool:
        vc = self.voice
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return False
        self.loop = False
        vc.stop()
        return True

    def previous(self) -> bool:
        """يعود للأغنية السابقة (ثم تكمل الحالية بعدها). False إن لم يوجد تاريخ."""
        if not self.history:
            return False
        prev = self.history.pop()
        if self.current is not None:
            self.queue.appendleft(self.current)
        self.queue.appendleft(prev)
        self._have.set()
        vc = self.voice
        if vc and (vc.is_playing() or vc.is_paused()):
            self._no_history = True       # الحالية عادت للقائمة، فلا تُسجَّل في التاريخ
            self.loop = False
            vc.stop()
        return True

    def toggle_pause(self) -> Optional[bool]:
        """يبدّل الإيقاف المؤقت. يرجع الحالة الجديدة (True = متوقف) أو None إن لم يكن هناك تشغيل."""
        vc = self.voice
        if not vc:
            return None
        if vc.is_playing():
            vc.pause()
            return True
        if vc.is_paused():
            vc.resume()
            return False
        return None

    def set_volume(self, percent: int) -> int:
        percent = max(MIN_VOLUME, min(MAX_VOLUME, int(percent)))
        self.volume = percent / 100
        vc = self.voice
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = self.volume
        return percent

    def shuffle(self) -> None:
        items = list(self.queue)
        random.shuffle(items)
        self.queue = deque(items)

    def remove(self, uid: str) -> Optional[Track]:
        for t in self.queue:
            if t.uid == uid:
                self.queue.remove(t)
                return t
        return None

    def clear_queue(self) -> int:
        n = len(self.queue)
        self.queue.clear()
        return n

    # ------------------------------------------------------------------ اللوحة
    async def panel_payload(self, *, idle: bool = False) -> tuple[discord.Embed, Optional[bytes]]:
        t = self.current
        s = await self.cog.bot.db.get_settings(self.guild.id)
        nxt = self.queue[0].title if self.queue else None
        if s["info_cards"] and cards.available():
            try:
                thumb = await images.fetch(t.thumbnail) if (t and t.thumbnail) else None
                total = t.duration if t else 0
                png = await images.branded(
                    self.guild, cards.render_nowplaying, title=t.title if t else "", requester=t.requester.display_name if t else "",
                    duration=t.length if t else "—", thumbnail=thumb, queue_len=len(self.queue),
                    volume=int(self.volume * 100), loop=self.loop, guild_name=self.guild.name,
                    artist=t.artist if t else None, artist_is_channel=t.by_channel if t else False,
                    elapsed=self.elapsed() if t else None, total=total, paused=self.paused, next_title=nxt,
                    idle=idle or t is None)
                e = ui.info(f"{ui.Icons.MUSIC} {t.title}"[:250] if t and not idle else f"{ui.Icons.MUSIC} لوحة الموسيقى",
                            f"[رابط الأغنية]({t.url})" if t and not idle else "انتهت قائمة التشغيل.", ui.Colors.MUSIC)
                return e, png
            except Exception:  # noqa: BLE001
                log.exception("Now-playing card failed; using text embed")
        return self.text_embed(idle=idle), None

    def queue_text(self, rows: int = QUEUE_INLINE) -> str:
        """الطابور كقائمة رأسية: الأغنية الحالية مميّزة ثم القادمة بأرقامها (رقم · عنوان · مدة · الطالب)."""
        lines = []
        total = sum(t.duration for t in self.queue)
        lines.append(f"### 📃 الطابور · {len(self.queue)} أغنية" + (f" · {total // 60} دقيقة" if total else ""))
        if self.current is not None:
            lines.append(f"▶️ **الآن** · {self.current.title[:52]} · `{self.current.length}`")
        shown = list(self.queue)[:rows]
        for i, t in enumerate(shown, 1):
            lines.append(f"`{i:>2}` {t.title[:46]} · `{t.length}` · {t.requester.display_name[:16]}")
        if len(self.queue) > rows:
            lines.append(f"*و {len(self.queue) - rows} أغنية أخرى — اضغط «القائمة الكاملة»*")
        elif not self.queue:
            lines.append("*لا توجد أغانٍ قادمة — أضف أغنية بـ `/play`*")
        return "\n".join(lines)[:3800]

    def summary_text(self, *, idle: bool = False) -> str:
        """بديل نصي للبطاقة المصوّرة (عند تعطيلها أو تعذّر رسمها)."""
        t = self.current
        if t is None or idle:
            return f"### {ui.Icons.MUSIC} لوحة الموسيقى\nانتهت قائمة التشغيل."
        head = "⏸️ متوقف مؤقتًا" if self.paused else "يتم التشغيل الآن"
        parts = [f"### {ui.Icons.MUSIC} {head}", f"**[{t.title}]({t.url})**"]
        if t.artist:
            parts.append(("📻 القناة: " if t.by_channel else "🎤 الفنان: ") + t.artist)
        parts.append(f"⏱️ `{t.length}` · 🔊 `{int(self.volume * 100)}%` · 🙋 {t.requester.display_name}"
                     + (" · 🔁 تكرار" if self.loop else ""))
        return "\n".join(parts)

    def text_embed(self, *, idle: bool = False) -> discord.Embed:
        t = self.current
        if t is None or idle:
            return ui.info(f"{ui.Icons.MUSIC} لوحة الموسيقى", "لا شيء يعمل الآن.", ui.Colors.MUSIC)
        e = ui.info(f"{ui.Icons.MUSIC} {'⏸️ متوقف مؤقتًا' if self.paused else 'يتم التشغيل الآن'}",
                    f"**[{t.title}]({t.url})**", ui.Colors.MUSIC)
        e.add_field(name="⏱️ المدة", value=t.length)
        e.add_field(name="🙋 بطلب من", value=t.requester.mention)
        e.add_field(name="🔊 الصوت", value=f"{int(self.volume * 100)}%")
        if t.artist:
            e.add_field(name="📻 القناة" if t.by_channel else "🎤 الفنان", value=t.artist)
        if self.loop:
            e.add_field(name="🔁 التكرار", value="مفعّل")
        if self.queue:
            e.set_footer(text=f"التالي: {self.queue[0].title[:80]} • في القائمة {len(self.queue)}")
        if t.thumbnail:
            e.set_thumbnail(url=t.thumbnail)
        return e

    async def now_playing_message(self) -> dict:
        """رسالة «الآن» للأمر nowplaying (بدون أزرار)."""
        e, png = await self.panel_payload()
        out: dict = {"embed": e}
        if png:
            out["file"] = ui.card_file(png, "nowplaying.png", f"يعمل الآن: {self.current.title}")
        return out

    def now_playing_embed(self) -> discord.Embed:
        return self.text_embed()

    def request_panel(self) -> None:
        """يطلب تحديث اللوحة بتأخير مجمَّع (لا يُرسل أكثر من تحديث كل ثانية تقريبًا)."""
        if self.panel_message is None or (self._panel_task and not self._panel_task.done()):
            return
        self._panel_task = asyncio.create_task(self._panel_worker())

    async def _panel_worker(self) -> None:
        await asyncio.sleep(max(0.0, PANEL_MIN_INTERVAL - (time.monotonic() - self._last_panel)))
        await self.refresh_panel()

    async def refresh_panel(self, *, force: bool = False, idle: bool = False) -> None:
        """يحدّث رسالة اللوحة في مكانها، وينشئها مرة واحدة فقط أول مرة (أو إن حُذفت)."""
        async with self._panel_lock:
            _embed, png = await self.panel_payload(idle=idle)
            if self.panel_view is None:
                self.panel_view = MusicPanelView(self)
            view = self.panel_view
            view.render(has_image=png is not None, idle=idle)
            if self.panel_message is not None:
                kwargs: dict = {"view": view,
                                "attachments": [ui.card_file(png, "nowplaying.png", "لوحة الموسيقى")] if png else []}
                try:
                    await self.panel_message.edit(**kwargs)
                    self._last_panel = time.monotonic()
                    if idle:
                        self.panel_message = None
                    return
                except discord.NotFound:
                    self.panel_message = None      # حُذفت اللوحة: تُنشأ من جديد أدناه
                except discord.HTTPException:
                    log.warning("Could not edit the music panel", exc_info=True)
                    return
            if idle or self.text_channel is None or self.current is None:
                return
            kwargs = {"view": view}
            if png:
                kwargs["file"] = ui.card_file(png, "nowplaying.png", "لوحة الموسيقى")
            self.panel_message = await ui.safe_send(self.text_channel, **kwargs)
            self._last_panel = time.monotonic()

    async def destroy(self, message: Optional[str] = None, *, idle: bool = False):
        self.queue.clear()
        self.current = None
        vc = self.voice
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()
        if vc and await voice_home.home_channel(self.cog.bot, self.guild) is None:
            await vc.disconnect(force=True)       # مع الاتصال الدائم يبقى البوت في روم المنزل
        if self.panel_message is not None:
            await self.refresh_panel(force=True, idle=True)
        if self.panel_view is not None:
            self.panel_view.stop()
            self.panel_view = None
        if message and self.text_channel:
            await ui.safe_send(self.text_channel, embed=ui.info(message, color=ui.Colors.LOG))
        self.cog.players.pop(self.guild.id, None)
        if self.task is not asyncio.current_task():
            self.task.cancel()


# ====================================================================== اللوحة التفاعلية
class _PanelButton(discord.ui.Button):
    def __init__(self, action: str, label: str, emoji: str, style=discord.ButtonStyle.secondary,
                 disabled: bool = False):
        super().__init__(label=label or None, emoji=emoji, style=style, disabled=disabled)
        self.action = action

    async def callback(self, interaction: discord.Interaction):
        await self.view.handle(interaction, self.action)


class MusicPanelView(discord.ui.LayoutView):
    """لوحة الموسيقى (Components V2): بطاقة الأغنية ← أزرار التحكم ← الطابور. كل ضغطة تُفحص صلاحيتها من الخادم
    وتُنفَّذ على الحالة الفعلية لمشغّل الصوت."""

    CONTROL = {"previous", "skip", "stop", "loop", "vol_down", "vol_up"}

    def __init__(self, player: GuildPlayer):
        super().__init__(timeout=None)
        self.player = player
        self.message: Optional[discord.Message] = None
        self.render(has_image=False, idle=False)

    def render(self, *, has_image: bool, idle: bool) -> None:
        """يعيد بناء مكوّنات اللوحة من الحالة الحالية (بطاقة، أزرار، طابور)."""
        p = self.player
        self.clear_items()
        box = discord.ui.Container(accent_colour=discord.Colour(ui.Colors.MUSIC))
        if has_image:
            box.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://nowplaying.png",
                                                                          description="لوحة الموسيقى")))
        else:
            box.add_item(discord.ui.TextDisplay(p.summary_text(idle=idle)))
        if not idle:
            B = discord.ButtonStyle
            box.add_item(discord.ui.ActionRow(
                _PanelButton("previous", "السابق", "⏮️"),
                _PanelButton("pause", "استئناف" if p.paused else "إيقاف مؤقت", "▶️" if p.paused else "⏸️", B.primary),
                _PanelButton("skip", "تخطي", "⏭️"),
                _PanelButton("stop", "إيقاف", "⏹️", B.danger)))
            box.add_item(discord.ui.ActionRow(
                _PanelButton("vol_down", "", "🔉"),
                _PanelButton("noop", f"{int(p.volume * 100)}%", "🔊", disabled=True),
                _PanelButton("vol_up", "", "🔊"),
                _PanelButton("loop", "تكرار", "🔁", B.success if p.loop else B.secondary),
                _PanelButton("queue", "القائمة الكاملة", "📃")))
            box.add_item(discord.ui.Separator())
            box.add_item(discord.ui.TextDisplay(p.queue_text()))
        self.add_item(box)

    def sync(self) -> None:   # توافق مع الاستدعاءات القديمة: إعادة البناء تتم في render
        self.render(has_image=True, idle=False)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True   # الفحص التفصيلي لكل زر داخل handle

    async def on_error(self, interaction: discord.Interaction, exc: Exception, item: discord.ui.Item) -> None:
        log.exception("Music panel error", exc_info=exc)
        try:
            await ui.reply(interaction, ui.error("حدث خطأ غير متوقع أثناء تنفيذ هذا الإجراء."), ephemeral=True)
        except discord.HTTPException:
            pass

    async def handle(self, interaction: discord.Interaction, action: str) -> None:
        p, cog = self.player, self.player.cog
        ok, why = await cog.panel_guard(interaction, p, control=action in self.CONTROL)
        if not ok:
            return await interaction.response.send_message(embed=ui.error(why, "غير مسموح"), ephemeral=True)
        if action == "queue":
            view = QueueView(cog, p, interaction.user, await has_category(cog.bot.db, interaction.user, "music"))
            return await interaction.response.send_message(embed=view.embed(), view=view if view.children else None,
                                                           ephemeral=True)
        await interaction.response.defer()
        if action == "pause":
            p.toggle_pause()
        elif action == "skip":
            p.skip()
        elif action == "previous":
            if not p.previous():
                await interaction.followup.send(embed=ui.error("لا توجد أغنية سابقة."), ephemeral=True)
        elif action == "stop":
            await p.destroy("⏹️ أوقف " + interaction.user.mention + " الموسيقى.")
            return
        elif action == "loop":
            p.loop = not p.loop
        elif action == "vol_down":
            p.set_volume(int(p.volume * 100) - VOLUME_STEP)
        elif action == "vol_up":
            p.set_volume(int(p.volume * 100) + VOLUME_STEP)
        await p.refresh_panel(force=True)


# ====================================================================== عرض وإدارة القائمة
class _RemoveSelect(discord.ui.Select):
    def __init__(self, tracks: list[Track], start: int):
        super().__init__(placeholder="🗑️ اختر أغنية لإزالتها من القائمة...", row=0, options=[
            discord.SelectOption(label=f"{start + i + 1}. {t.title}"[:100], value=t.uid,
                                 description=f"بطلب من {t.requester.display_name}"[:100])
            for i, t in enumerate(tracks)])

    async def callback(self, interaction: discord.Interaction):
        await self.view.remove(interaction, self.values[0])


class QueueView(ui.BaseView):
    deny_message = "هذه القائمة خاصة بمن طلبها. اكتب /queue بنفسك."

    def __init__(self, cog: "Music", player: GuildPlayer, viewer: discord.Member, can_manage: bool):
        super().__init__(owner_id=viewer.id, timeout=180)
        self.cog, self.player, self.can_manage = cog, player, can_manage
        self.page = 0
        self.confirm_clear = False
        self._rebuild()

    @property
    def pages(self) -> int:
        return max(1, (len(self.player.queue) + QUEUE_PAGE - 1) // QUEUE_PAGE)

    def _chunk(self) -> list[Track]:
        self.page = max(0, min(self.page, self.pages - 1))
        return list(self.player.queue)[self.page * QUEUE_PAGE:(self.page + 1) * QUEUE_PAGE]

    def embed(self) -> discord.Embed:
        p = self.player
        chunk = self._chunk()
        start = self.page * QUEUE_PAGE
        lines = [f"`{start + i + 1}.` [{t.title[:60]}]({t.url}) • {t.length} • {t.requester.mention}"
                 for i, t in enumerate(chunk)]
        desc = (f"**الآن:** [{p.current.title}]({p.current.url}) — {p.current.requester.mention}\n\n"
                if p.current else "")
        desc += "\n".join(lines) or "لا يوجد شيء بعدها."
        e = ui.info(f"📃 قائمة الانتظار ({len(p.queue)})", desc, ui.Colors.MUSIC)
        total = sum(t.duration for t in p.queue)
        e.set_footer(text=f"صفحة {self.page + 1} من {self.pages}  •  المدة الإجمالية: {total // 60} دقيقة")
        return e

    def _rebuild(self) -> None:
        self.clear_items()
        chunk = self._chunk()
        if self.can_manage and chunk:
            self.add_item(_RemoveSelect(chunk, self.page * QUEUE_PAGE))
        if self.pages > 1:
            prev = discord.ui.Button(emoji="◀️", label="السابق", row=1, disabled=self.page == 0)
            nxt = discord.ui.Button(emoji="▶️", label="التالي", row=1, disabled=self.page >= self.pages - 1)
            prev.callback, nxt.callback = self._make_nav(-1), self._make_nav(1)
            self.add_item(prev)
            self.add_item(nxt)
        if self.can_manage and self.player.queue:
            clear = discord.ui.Button(emoji="⚠️" if self.confirm_clear else "🧹",
                                      label="اضغط للتأكيد" if self.confirm_clear else "مسح القائمة",
                                      style=discord.ButtonStyle.danger, row=1)
            clear.callback = self._clear
            self.add_item(clear)

    def _make_nav(self, delta: int):
        async def cb(interaction: discord.Interaction):
            self.page += delta
            self.confirm_clear = False
            self._rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)
        return cb

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        # فحص من الخادم عند كل إجراء إداري (لا يكفي أن الأزرار ظهرت)
        if not await has_category(self.cog.bot.db, interaction.user, "music") \
                or interaction.guild_id != self.player.guild.id:
            await interaction.response.send_message(embed=ui.error("إدارة القائمة لأصحاب صلاحية الموسيقى فقط."),
                                                    ephemeral=True)
            return False
        return True

    async def remove(self, interaction: discord.Interaction, uid: str) -> None:
        if not await self._authorized(interaction):
            return
        track = self.player.remove(uid)
        self.confirm_clear = False
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self if self.children else None)
        if track is not None:
            self.player.request_panel()

    async def _clear(self, interaction: discord.Interaction) -> None:
        if not await self._authorized(interaction):
            return
        if not self.confirm_clear:
            self.confirm_clear = True
            self._rebuild()
            return await interaction.response.edit_message(embed=self.embed(), view=self)
        n = self.player.clear_queue()
        self.confirm_clear = False
        self._rebuild()
        await interaction.response.edit_message(embed=self.embed(), view=self if self.children else None)
        self.player.request_panel()
        log.info("Queue cleared by %s (%d tracks)", interaction.user.id, n)


# ====================================================================== الأوامر
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    music = app_commands.Group(name="music", description="أوامر الموسيقى", guild_only=True)

    def get_player(self, guild: discord.Guild) -> GuildPlayer:
        if guild.id not in self.players:
            self.players[guild.id] = GuildPlayer(self, guild)
        return self.players[guild.id]

    # ---- فحوص الصلاحية (تُستخدم للأوامر وللأزرار)
    async def can_control(self, member: discord.Member, player: GuildPlayer) -> bool:
        """صاحب الأغنية الحالية أو من لديه صلاحية الموسيقى أو إذا كان وحده مع البوت."""
        if await has_category(self.bot.db, member, "music"):
            return True
        if player.current and player.current.requester.id == member.id:
            return True
        vc = player.voice
        if vc and vc.channel:
            return len([m for m in vc.channel.members if not m.bot]) <= 1
        return False

    async def panel_guard(self, interaction: discord.Interaction, player: GuildPlayer, *, control: bool):
        if interaction.guild_id != player.guild.id or not isinstance(interaction.user, discord.Member):
            return False, "هذه اللوحة تخص سيرفرًا آخر."
        vc = player.voice
        if not vc or not vc.is_connected():
            return False, "البوت غير متصل بأي روم صوتي."
        user_ch = interaction.user.voice.channel if interaction.user.voice else None
        if user_ch is None or user_ch != vc.channel:
            return False, "يجب أن تكون في نفس الروم الصوتي مع البوت."
        if control and not await self.can_control(interaction.user, player):
            return False, "هذا الإجراء لصاحب الأغنية أو لمن يملك صلاحية التحكم بالموسيقى."
        return True, ""

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
        if await self.can_control(interaction.user, player):
            return True
        await ui.reply(interaction, ui.error("هذا الإجراء لصاحب الأغنية أو لمن يملك صلاحية التحكم بالموسيقى."),
                       ephemeral=True)
        return False

    async def ensure_connected(self, player: GuildPlayer, channel) -> None:
        """اتصال صوتي آمن من السباق: طلبان متزامنان لا يتسببان بخطأ «متصل مسبقًا»."""
        async with player.connect_lock:
            vc = player.voice
            if vc and vc.is_connected():
                return
            if vc:
                await vc.disconnect(force=True)
            try:
                await channel.connect(self_deaf=True, timeout=20.0, reconnect=True)
            except discord.ClientException:
                if not (player.voice and player.voice.is_connected()):
                    raise

    # ---- التشغيل
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
        player = self.get_player(interaction.guild)
        try:
            await self.ensure_connected(player, channel)
        except asyncio.TimeoutError:
            log.warning("Voice connect timed out in %s", channel.id)
            return await interaction.followup.send(embed=ui.error(
                "انتهت مهلة الاتصال بالروم الصوتي. تأكد من صلاحيات البوت وحاول مجددًا.", "تعذّر الاتصال"))
        except (discord.ClientException, discord.HTTPException) as exc:
            log.exception("Voice connect failed")
            return await interaction.followup.send(embed=ui.error(
                f"تعذّر الاتصال بالروم الصوتي ({type(exc).__name__}).", "تعذّر الاتصال"))
        player.text_channel = interaction.channel
        position = player.enqueue(track)
        if position is None:
            return await interaction.followup.send(embed=ui.error(f"قائمة الانتظار ممتلئة ({MAX_QUEUE} أغنية)."))
        if player.current:
            e = ui.info("➕ أُضيفت للقائمة", f"**[{track.title}]({track.url})**", ui.Colors.MUSIC)
            e.add_field(name="الترتيب", value=f"`#{position}`")
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

    # ---- التحكم
    @music.command(name="skip", description="تخطي الأغنية الحالية")
    async def skip(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not player.current:
            return await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)
        if not await self._can_control(interaction, player):
            return
        player.skip()
        await ui.reply(interaction, ui.info("⏭️ تم التخطي", color=ui.Colors.MUSIC))

    @music.command(name="previous", description="العودة إلى الأغنية السابقة")
    async def previous(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        if not player.previous():
            return await ui.reply(interaction, ui.error("لا توجد أغنية سابقة."), ephemeral=True)
        await ui.reply(interaction, ui.info("⏮️ عودة للأغنية السابقة", color=ui.Colors.MUSIC))

    @music.command(name="pause", description="إيقاف مؤقت")
    async def pause(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        vc = interaction.guild.voice_client
        if vc.is_playing():
            vc.pause()
            player = self.players.get(interaction.guild_id)
            if player:
                player.request_panel()
            return await ui.reply(interaction, ui.info("⏸️ إيقاف مؤقت", color=ui.Colors.MUSIC))
        await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)

    @music.command(name="resume", description="استئناف التشغيل")
    async def resume(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        vc = interaction.guild.voice_client
        if vc.is_paused():
            vc.resume()
            player = self.players.get(interaction.guild_id)
            if player:
                player.request_panel()
            return await ui.reply(interaction, ui.info("▶️ تم الاستئناف", color=ui.Colors.MUSIC))
        await ui.reply(interaction, ui.error("التشغيل ليس متوقفًا."), ephemeral=True)

    @music.command(name="stop", description="إيقاف الموسيقى ومسح القائمة والخروج")
    async def stop(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        await ui.reply(interaction, ui.info("⏹️ تم إيقاف الموسيقى", color=ui.Colors.MUSIC))
        await player.destroy()

    @music.command(name="volume", description="تغيير مستوى الصوت")
    @app_commands.describe(percent="مستوى الصوت بالنسبة المئوية (1–150)")
    async def volume(self, interaction: discord.Interaction, percent: app_commands.Range[int, 1, 150]):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        percent = player.set_volume(percent)
        player.request_panel()
        await ui.reply(interaction, ui.info(f"🔊 الصوت: {percent}%", color=ui.Colors.MUSIC))

    @music.command(name="loop", description="تكرار الأغنية الحالية")
    async def loop(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        player.loop = not player.loop
        player.request_panel()
        await ui.reply(interaction, ui.info("🔁 التكرار مفعّل" if player.loop else "➡️ التكرار معطّل",
                                            color=ui.Colors.MUSIC))

    @music.command(name="shuffle", description="خلط قائمة الانتظار")
    async def shuffle(self, interaction: discord.Interaction):
        if not await self._same_channel(interaction):
            return
        player = self.get_player(interaction.guild)
        if not await self._can_control(interaction, player):
            return
        player.shuffle()
        player.request_panel()
        await ui.reply(interaction, ui.info("🔀 تم خلط القائمة", color=ui.Colors.MUSIC))

    # ---- القائمة
    async def _show_queue(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or (not player.current and not player.queue):
            return await ui.reply(interaction, ui.info("📭 القائمة فارغة", color=ui.Colors.MUSIC), ephemeral=True)
        can_manage = await has_category(self.bot.db, interaction.user, "music")
        view = QueueView(self, player, interaction.user, can_manage)
        await ui.reply(interaction, view.embed(), view=view if view.children else None)
        if view.children:
            try:
                view.message = await interaction.original_response()
            except discord.HTTPException:
                pass

    @music.command(name="queue", description="عرض قائمة الانتظار (بصفحات)")
    async def queue(self, interaction: discord.Interaction):
        await self._show_queue(interaction)

    @app_commands.command(name="queue", description="عرض قائمة انتظار الموسيقى (بصفحات)")
    @app_commands.guild_only()
    async def queue_alias(self, interaction: discord.Interaction):
        await self._show_queue(interaction)

    @music.command(name="remove", description="إزالة أغنية من قائمة الانتظار (لأصحاب صلاحية الموسيقى)")
    @app_commands.describe(position="ترتيب الأغنية في القائمة (من /queue)")
    @require("music")
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 100]):
        player = self.players.get(interaction.guild_id)
        if not player or position > len(player.queue):
            return await ui.reply(interaction, ui.error("لا توجد أغنية بهذا الترتيب في القائمة."), ephemeral=True)
        track = player.remove(list(player.queue)[position - 1].uid)
        player.request_panel()
        await ui.reply(interaction, ui.success("أُزيلت من القائمة", f"**{track.title}** (كانت رقم {position})"))

    @music.command(name="clear", description="مسح قائمة الانتظار كاملة (لأصحاب صلاحية الموسيقى)")
    @require("music")
    async def clear(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or not player.queue:
            return await ui.reply(interaction, ui.error("القائمة فارغة."), ephemeral=True)
        n = player.clear_queue()
        player.request_panel()
        await ui.reply(interaction, ui.success("تم مسح القائمة", f"أُزيلت **{n}** أغنية (الأغنية الحالية تكمل)."))

    @music.command(name="nowplaying", description="الأغنية الحالية")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or not player.current:
            return await ui.reply(interaction, ui.error("لا يوجد شيء يعمل."), ephemeral=True)
        msg = await player.now_playing_message()
        await ui.reply(interaction, msg["embed"], file=msg.get("file"))

    @music.command(name="panel", description="إظهار لوحة التحكم بالموسيقى في هذا الروم")
    async def panel(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if not player or not player.current:
            return await ui.reply(interaction, ui.error("لا يوجد شيء يعمل الآن."), ephemeral=True)
        if not await self._same_channel(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        old = player.panel_message
        player.panel_message = None            # لوحة جديدة في هذا الروم
        player.text_channel = interaction.channel
        await player.refresh_panel(force=True)
        if old is not None:
            try:
                await old.delete()          # رسائل Components V2 لا يمكن تجريدها من المكوّنات: تُحذف
            except discord.HTTPException:
                pass
        await interaction.followup.send(embed=ui.success("تم", "ظهرت لوحة التحكم في هذا الروم."), ephemeral=True)

    # ---- أحداث الصوت
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if self.bot.user and member.id == self.bot.user.id:
            # طُرد البوت أو انقطع (ولا يوجد اتصال دائم): أنهِ الجلسة فلا تبقى حالة معلّقة
            if before.channel and after.channel is None:
                player = self.players.get(member.guild.id)
                if player and await voice_home.home_channel(self.bot, member.guild) is None:
                    await player.destroy("👋 انقطع الاتصال الصوتي وأُنهيت الجلسة.", idle=True)
            return
        vc = member.guild.voice_client
        if not vc or not before.channel or before.channel != vc.channel:
            return
        if await voice_home.home_channel(self.bot, member.guild) is not None:
            return   # اتصال دائم: لا يغادر عند خلو الروم
        if all(m.bot for m in vc.channel.members):
            await asyncio.sleep(60)
            vc = member.guild.voice_client
            if vc and all(m.bot for m in vc.channel.members):
                player = self.players.get(member.guild.id)
                if player:
                    await player.destroy("👋 غادرت لأن الروم الصوتي أصبح فارغًا.", idle=True)
                else:
                    await vc.disconnect(force=True)

    async def cog_unload(self):
        for p in list(self.players.values()):
            await p.destroy()


async def setup(bot):
    await bot.add_cog(Music(bot))

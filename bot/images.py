"""جلب الصور الخارجية بأمان (صور المنتجات، الأغلفة المصغّرة) والأفاتارات مع تخزين مؤقت.

حماية SSRF (لأن الرابط يُدخله مسؤول/مصدر خارجي): https فقط، رفض العناوين غير العامة (محلية/خاصة/ربط محلي)،
بلا تحويلات تلقائية، سقف للحجم والمهلة، وتحقق أن المحتوى صورة فعلًا عبر Pillow. أي فشل يرجع None
فيُعرض بديل (أيقونة) ولا يتعطل الأمر.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import io
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from .brand import BRAND

log = logging.getLogger("bot.images")

MAX_BYTES = 4_000_000
TIMEOUT = 6.0
CACHE_TTL = 900
CACHE_MAX = 96
_cache: "OrderedDict[str, tuple[float, Optional[bytes]]]" = OrderedDict()


def validate_url(raw: str) -> str:
    """يتحقق من رابط صورة أدخله المسؤول. يرجع الرابط المنظّف أو '' إن كان فارغًا، ويرفع ValueError عند الخطأ."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if len(raw) > 400:
        raise ValueError("رابط الصورة طويل جدًا (الحد 400 حرف).")
    u = urlparse(raw)
    if u.scheme != "https" or not u.hostname:
        raise ValueError("رابط الصورة يجب أن يبدأ بـ https:// ويكون رابطًا مباشرًا لصورة.")
    if u.username or u.password:
        raise ValueError("رابط الصورة لا يجوز أن يحتوي بيانات دخول.")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if not ip.is_global:
            raise ValueError("رابط الصورة يشير لعنوان داخلي غير مسموح.")
    except ValueError as exc:
        if "غير مسموح" in str(exc):
            raise
    return raw


async def _is_public_host(host: str) -> bool:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=4)
    except (OSError, asyncio.TimeoutError):
        return False
    addrs = {i[4][0] for i in infos}
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


def _remember(url: str, data: Optional[bytes]) -> None:
    _cache[url] = (time.monotonic(), data)
    _cache.move_to_end(url)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


async def fetch(url: str) -> Optional[bytes]:
    """يحمّل صورة من رابط عام. يرجع bytes (PNG/JPEG/GIF/WEBP صالح) أو None."""
    if not url:
        return None
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        return hit[1]
    data: Optional[bytes] = None
    try:
        validate_url(url)
        host = urlparse(url).hostname
        if not await _is_public_host(host):
            raise ValueError("non-public host")
        timeout = aiohttp.ClientTimeout(total=TIMEOUT, connect=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=False, headers={"User-Agent": "DiscordBotImageFetch/1.0"}) as r:
                if r.status != 200:
                    raise ValueError(f"status {r.status}")
                if not (r.headers.get("Content-Type", "").lower().startswith("image/")):
                    raise ValueError("not an image")
                if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
                    raise ValueError("too large")
                buf = bytearray()
                async for chunk in r.content.iter_chunked(64 * 1024):
                    buf.extend(chunk)
                    if len(buf) > MAX_BYTES:
                        raise ValueError("too large")
        Image.open(io.BytesIO(bytes(buf))).verify()    # المحتوى صورة سليمة فعلًا
        data = bytes(buf)
    except Exception as exc:  # noqa: BLE001 - أي فشل = بديل بصري، لا تعطيل للأمر
        log.info("Image fetch skipped (%s): %s", type(exc).__name__, str(exc)[:120])
        data = None
    _remember(url, data)
    return data


_avatar_cache: "OrderedDict[tuple[int, int], tuple[float, Optional[bytes]]]" = OrderedDict()


async def avatar(user, size: int = 128) -> Optional[bytes]:
    """صورة العضو (مع تخزين مؤقت 10 دقائق). يرجع None عند الفشل فيُرسم حرف أول الاسم بدلها."""
    key = (user.id, size)
    hit = _avatar_cache.get(key)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    try:
        data = await asyncio.wait_for(user.display_avatar.with_size(size).read(), timeout=5)
    except Exception:  # noqa: BLE001
        data = None
    _avatar_cache[key] = (time.monotonic(), data)
    _avatar_cache.move_to_end(key)
    while len(_avatar_cache) > 256:
        _avatar_cache.popitem(last=False)
    return data


_guild_icons: "OrderedDict[tuple, tuple[float, Optional[bytes]]]" = OrderedDict()


async def guild_icon(guild) -> Optional[bytes]:
    """شعار السيرفر (64px) مع تخزين مؤقت ساعة؛ None إن لم يوجد أو تعذّر تحميله."""
    icon = getattr(guild, "icon", None)
    key = getattr(icon, "key", None)
    if not isinstance(key, str):
        return None
    ck = (getattr(guild, "id", 0), key)
    hit = _guild_icons.get(ck)
    if hit and time.monotonic() - hit[0] < 3600:
        return hit[1]
    try:
        data = await asyncio.wait_for(icon.with_size(64).read(), timeout=5)
        if not isinstance(data, (bytes, bytearray)):
            data = None
    except Exception:  # noqa: BLE001
        data = None
    _guild_icons[ck] = (time.monotonic(), bytes(data) if data else None)
    while len(_guild_icons) > 16:
        _guild_icons.popitem(last=False)
    return _guild_icons[ck][1]


async def branded(guild, fn, *args, **kwargs):
    """يشغّل دالة الرسم في خيط منفصل مع شعار السيرفر في سياقها (فيظهر في موضعه الثابت في كل الصور)."""
    icon = await guild_icon(guild) if guild is not None else None
    ctx = contextvars.copy_context()
    ctx.run(BRAND.set, icon)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(ctx.run, functools.partial(fn, *args, **kwargs)))

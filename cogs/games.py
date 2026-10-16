"""الألعاب والفعاليات المرتبطة بنظام النقاط.

لإضافة لعبة جديدة مستقبلًا:
1) أضف تعريفها في GAMES بالأسفل.
2) أضف إعداداتها في bot/settings_schema.py: reward_<name> و game_<name>_enabled (وتظهر تلقائيًا في /panel).
3) اكتب أمرًا يستدعي self._prepare(...) ثم self._award(...) للفائز، و self._card(...) لعرض البطاقة.
"""
from __future__ import annotations

import asyncio
import io
import json
import random
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot import ui
from bot.checks import has_category, require
from bot.game_data import QUESTIONS, WORDS
from bot.game_logic import (GuessState, make_math_problem, parse_number, quiz_options, scramble,
                            scramble_hint)
from bot.utils import earning_block_reason, has_invisible, normalize, obfuscate
from bot import wheel


@dataclass(frozen=True)
class GameInfo:
    name: str
    emoji: str
    about: str
    howto: str
    seconds_key: Optional[str] = None


GAMES: dict[str, GameInfo] = {
    "roulette": GameInfo("الروليت", "🎡", "عجلة حظ بأسماء المشاركين — العجلة تختار الفائز",
                         "اضغط «دخول» للمشاركة ثم انتظر دوران العجلة", "roulette_join_seconds"),
    "typing": GameInfo("أسرع كتابة", "⌨️", "أول من يكتب الكلمة الظاهرة يفوز",
                       "اكتب الكلمة في الشات بنفسك — النسخ واللصق لا يُحتسب", "typing_seconds"),
    "quiz": GameInfo("سؤال سريع", "❓", "سؤال معلومات عامة بأربعة خيارات",
                     "اضغط على الإجابة الصحيحة — محاولة واحدة لكل عضو", "quiz_seconds"),
    "guess": GameInfo("خمّن الرقم", "🔢", "خمّن الرقم السري مع تلميحات أكبر/أصغر",
                      "اكتب رقمًا في الشات — ⬆️ الرقم أكبر • ⬇️ الرقم أصغر", "guess_seconds"),
    "scramble": GameInfo("فكّك الكلمة", "🧩", "رتّب الحروف المبعثرة لتكوين الكلمة",
                         "اكتب الكلمة الصحيحة في الشات — يظهر تلميح في منتصف الوقت", "scramble_seconds"),
    "math": GameInfo("الحساب السريع", "🧮", "حل المسألة الحسابية قبل الجميع",
                     "اكتب الناتج في الشات (يقبل الأرقام العربية)", "math_seconds"),
}
GAME_NAMES = {k: g.name for k, g in GAMES.items()}

RewardOpt = Optional[app_commands.Range[int, 0, 1_000_000]]
_rng = secrets.SystemRandom()


@dataclass
class GameContext:
    game: str
    reward: int
    is_event: bool
    starter: discord.Member
    channel_id: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task: Optional[asyncio.Task] = None
    message: Optional[discord.Message] = None
    card: Optional[discord.Embed] = None
    views: list[discord.ui.View] = field(default_factory=list)
    helpers: list[asyncio.Task] = field(default_factory=list)
    awarded: bool = False


class Games(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active: dict[int, GameContext] = {}      # channel_id -> لعبة قائمة
        self.last_game: dict[int, float] = {}         # channel_id -> وقت آخر لعبة

    game = app_commands.Group(name="game", description="الألعاب والفعاليات", guild_only=True)

    # ================================================================== أدوات مشتركة
    async def _prepare(self, interaction: discord.Interaction, game: str,
                       custom_reward: Optional[int]) -> Optional[GameContext]:
        db = self.bot.db
        s = await db.get_settings(interaction.guild_id)
        is_host = await has_category(db, interaction.user, "events")
        channel = interaction.channel

        async def deny(msg: str, title: str = "لا يمكن بدء اللعبة"):
            await ui.reply(interaction, ui.error(msg, title), ephemeral=True)
            return None

        if not s.get(f"game_{game}_enabled", True):
            return await deny(f"لعبة **{GAMES[game].name}** معطّلة حاليًا من الإدارة.")
        if s["games_channel"] and channel.id != s["games_channel"] and not is_host:
            return await deny(f"الألعاب متاحة فقط في <#{s['games_channel']}>.")
        if not s["games_public"] and not is_host:
            return await deny("بدء الألعاب متاح لمنظّمي الفعاليات فقط.")
        if channel.id in self.active:
            current = GAMES[self.active[channel.id].game]
            return await deny(f"توجد لعبة **{current.emoji} {current.name}** قائمة في هذا الروم، انتظر حتى تنتهي.")
        wait = s["game_cooldown"] - (time.monotonic() - self.last_game.get(channel.id, 0))
        if wait > 0 and not is_host:
            return await deny(f"انتظر **{wait:.0f}** ثانية قبل بدء لعبة جديدة هنا.", "تمهّل قليلًا")
        if custom_reward is not None and not is_host:
            return await deny("تحديد مكافأة مخصصة متاح لمنظّمي الفعاليات فقط.")
        if custom_reward is not None and custom_reward > s["max_event_reward"]:
            return await deny(f"أقصى مكافأة مسموحة للفعالية هي **{s['max_event_reward']:,}**.")

        reward = custom_reward if custom_reward is not None else s[f"reward_{game}"]
        # لا يوجد await بين الفحص والحجز، فلا يمكن بدء لعبتين في نفس الروم بالتزامن
        ctx = GameContext(game=game, reward=reward, is_event=custom_reward is not None, starter=interaction.user,
                          channel_id=channel.id, task=asyncio.current_task())
        self.active[channel.id] = ctx
        return ctx

    def _finish(self, ctx: GameContext):
        if self.active.get(ctx.channel_id) is ctx:
            self.active.pop(ctx.channel_id, None)
            self.last_game[ctx.channel_id] = time.monotonic()
        for v in ctx.views:
            v.stop()
        for t in ctx.helpers:
            t.cancel()

    def _card(self, ctx: GameContext, s: dict, body: str, *, ends_at: Optional[int] = None,
              footer: Optional[str] = None) -> discord.Embed:
        """بطاقة اللعبة الموحّدة: العنوان، المحتوى، الجائزة، والوقت المتبقي."""
        info = GAMES[ctx.game]
        title = f"{info.emoji}  {info.name}" + ("  ·  🎉 فعالية خاصة" if ctx.is_event else "")
        e = ui.info(title, body, ui.Colors.GOLD if ctx.is_event else ui.Colors.GAME)
        e.add_field(name="🏆 الجائزة", value=ui.money(ctx.reward, s) if ctx.reward else "`بدون جائزة`")
        if ends_at:
            e.add_field(name="⏱️ ينتهي", value=ui.countdown(ends_at))
        e.set_footer(text=footer or info.howto)
        ctx.card = e
        return e

    async def _close(self, ctx: GameContext, status: str, view: Optional[discord.ui.View] = None) -> None:
        """تحويل بطاقة اللعبة إلى حالة «انتهت» حتى لا يحاول أحد الإجابة عليها."""
        if not ctx.card:
            return
        e = ctx.card.copy()
        e.colour = ui.Colors.LOG
        for i, f in enumerate(e.fields):
            if f.name.startswith("⏱️"):
                e.set_field_at(i, name="⏹️ الحالة", value=status, inline=True)
                break
        else:
            e.add_field(name="⏹️ الحالة", value=status, inline=True)
        e.set_footer(text="انتهت اللعبة — ابدأ جولة جديدة من /game")
        await ui.safe_edit(ctx.message, embed=e, view=view)

    async def _send_card(self, interaction: discord.Interaction, ctx: GameContext, embed: discord.Embed,
                         view: Optional[discord.ui.View] = None, file: Optional[discord.File] = None):
        await ui.reply(interaction, embed, view=view, file=file)
        try:
            ctx.message = await interaction.original_response()
        except discord.HTTPException:
            ctx.message = None
        if view is not None:
            ctx.views.append(view)
            if isinstance(view, ui.BaseView):
                view.message = ctx.message

    async def _award(self, guild: discord.Guild, member: discord.Member, ctx: GameContext) -> str:
        """يمنح الفائز نقاطه مرة واحدة فقط، بعد التحقق من الأهلية والحد اليومي، ويرجع سطر النتيجة."""
        if ctx.awarded or ctx.reward <= 0:
            return ""
        ctx.awarded = True  # حماية فورية داخل نفس العملية قبل أي await
        db = self.bot.db
        s = await db.get_settings(guild.id)
        reason = earning_block_reason(member, s)
        if reason:
            return f"\n\n{ui.Icons.WARNING} لم تُضف نقاط: {reason}"
        cap = 0 if ctx.is_event else s["daily_earn_cap"]  # فعاليات المنظّمين لا تخضع للحد اليومي
        granted = await db.game_reward(guild.id, member.id, ctx.reward, GAME_NAMES[ctx.game], cap,
                                       claim_key=f"game:{guild.id}:{ctx.id}")
        if granted is None:  # صُرفت مسبقًا لهذه الجولة
            return ""
        if granted == 0:
            return f"\n\n{ui.Icons.WARNING} وصلت للحد اليومي من نقاط الألعاب، عُد غدًا!"
        note = "  ·  بلغت الحد اليومي" if granted < ctx.reward else ""
        bal = await db.balance(guild.id, member.id)
        if ctx.is_event or granted >= 100:
            await self.bot.send_log(guild, "economy", ui.info(
                "🎮 مكافأة لعبة", f"{member.mention} ربح `{granted:,}` في **{GAME_NAMES[ctx.game]}**"
                                 + (f" (فعالية بواسطة {ctx.starter.mention})" if ctx.is_event else ""),
                ui.Colors.LOG))
        return f"\n\n{ui.delta(granted, s)} {s['currency_name']}{note}  ·  الرصيد الآن **{bal:,}**"

    def _win_embed(self, ctx: GameContext, winner: discord.Member, headline: str,
                   details: list[tuple[str, str]], reward_line: str) -> discord.Embed:
        info = GAMES[ctx.game]
        e = ui.info(f"{ui.Icons.TROPHY}  {winner.display_name} فاز!",
                    f"{headline}\n\n{ui.kv(details)}{reward_line}", ui.Colors.GOLD)
        e.set_thumbnail(url=winner.display_avatar.url)
        e.set_footer(text=f"{info.emoji} {info.name}" + ("  ·  فعالية خاصة" if ctx.is_event else ""))
        return e

    @staticmethod
    def _timeout_embed(ctx: GameContext, reveal: str) -> discord.Embed:
        info = GAMES[ctx.game]
        e = ui.info(f"⌛  انتهى الوقت — {info.name}", f"لم يفز أحد هذه المرة.\n{reveal}", ui.Colors.LOG)
        e.set_footer(text="جرّب جولة جديدة من /game")
        return e

    @staticmethod
    async def _announce(channel: discord.abc.Messageable, embed: discord.Embed,
                        reply_to: Optional[discord.Message] = None, content: Optional[str] = None):
        if reply_to is not None:
            try:
                return await reply_to.reply(embed=embed, content=content, mention_author=False)
            except discord.HTTPException:
                pass  # الرسالة حُذفت مثلًا
        return await ui.safe_send(channel, embed=embed, content=content)

    async def _wait_message(self, check, timeout: float) -> Optional[discord.Message]:
        if timeout <= 0:
            return None
        try:
            return await self.bot.wait_for("message", check=check, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    @staticmethod
    async def _react(msg: discord.Message, emoji: str):
        try:
            await msg.add_reaction(emoji)
        except discord.HTTPException:
            pass

    async def _word_pool(self, guild_id: int) -> list[str]:
        custom = [r["content"] for r in await self.bot.db.custom(guild_id, "word")]
        return WORDS + custom

    async def _question_pool(self, guild_id: int) -> list[tuple[str, str, list[str]]]:
        pool = list(QUESTIONS)
        for r in await self.bot.db.custom(guild_id, "question"):
            try:
                pool.append((r["content"], r["answer"], json.loads(r["options"])))
            except (TypeError, ValueError):
                continue
        return pool

    # ================================================================== أسرع كتابة
    @game.command(name="typing", description="أسرع شخص يكتب الكلمة يفوز")
    @app_commands.describe(reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def typing(self, interaction: discord.Interaction, reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "typing", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            seconds = s["typing_seconds"]
            word = _rng.choice(await self._word_pool(interaction.guild_id))
            ends = int(time.time()) + seconds
            body = f"اكتب الكلمة التالية بأسرع ما يمكن:\n# {obfuscate(word)}\n-# {len(word)} حرف"
            await self._send_card(interaction, ctx, self._card(ctx, s, body, ends_at=ends))
            started = time.monotonic()
            target = normalize(word)
            min_time = 0.6 + 0.07 * len(word)  # أسرع من هذا = غير بشري

            def check(m: discord.Message):
                if m.channel.id != interaction.channel_id or m.author.bot:
                    return False
                if has_invisible(m.content) or normalize(m.content) != target:
                    return False
                return time.monotonic() - started >= min_time

            msg = await self._wait_message(check, seconds)
            if not msg:
                await self._close(ctx, "لم يكتبها أحد")
                return await self._announce(interaction.channel, self._timeout_embed(ctx, f"**الكلمة:** {word}"))
            elapsed = time.monotonic() - started
            await self._react(msg, "⚡")
            extra = await self._award(interaction.guild, msg.author, ctx)
            await self._close(ctx, f"فاز {msg.author.mention}")
            cps = len(word) / elapsed if elapsed else 0
            await self._announce(interaction.channel, self._win_embed(
                ctx, msg.author, f"{msg.author.mention} كان الأسرع!",
                [("الكلمة", f"`{word}`"), ("الوقت", f"`{elapsed:.2f}` ثانية"), ("السرعة", f"`{cps:.1f}` حرف/ث")],
                extra), reply_to=msg)
        finally:
            self._finish(ctx)

    # ================================================================== فكّك الكلمة
    @game.command(name="scramble", description="رتّب حروف الكلمة المبعثرة")
    @app_commands.describe(reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def scramble_cmd(self, interaction: discord.Interaction, reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "scramble", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            seconds = s["scramble_seconds"]
            pool = [w for w in await self._word_pool(interaction.guild_id) if " " not in w and len(w) >= 4]
            word = _rng.choice(pool or WORDS)
            mixed = scramble(word, _rng)
            ends = int(time.time()) + seconds
            body = f"رتّب الحروف لتكوين كلمة صحيحة:\n# {'  '.join(mixed)}"
            await self._send_card(interaction, ctx, self._card(ctx, s, body, ends_at=ends))
            started = time.monotonic()
            target = normalize(word)

            def check(m: discord.Message):
                return (m.channel.id == interaction.channel_id and not m.author.bot
                        and not has_invisible(m.content) and normalize(m.content) == target)

            msg = await self._wait_message(check, seconds / 2)
            if not msg:  # تلميح في منتصف الوقت
                hinted = self._card(ctx, s, body, ends_at=ends)
                hinted.add_field(name="💡 تلميح", value=scramble_hint(word), inline=False)
                await ui.safe_edit(ctx.message, embed=hinted)
                msg = await self._wait_message(check, seconds - (time.monotonic() - started))
            if not msg:
                await self._close(ctx, "لم يفكّها أحد")
                return await self._announce(interaction.channel, self._timeout_embed(ctx, f"**الكلمة:** {word}"))
            elapsed = time.monotonic() - started
            await self._react(msg, "🧩")
            extra = await self._award(interaction.guild, msg.author, ctx)
            await self._close(ctx, f"فاز {msg.author.mention}")
            await self._announce(interaction.channel, self._win_embed(
                ctx, msg.author, f"{msg.author.mention} فكّك الكلمة!",
                [("الكلمة", f"`{word}`"), ("الوقت", f"`{elapsed:.2f}` ثانية")], extra), reply_to=msg)
        finally:
            self._finish(ctx)

    # ================================================================== الحساب السريع
    @game.command(name="math", description="أول من يحل المسألة يفوز")
    @app_commands.describe(reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def math(self, interaction: discord.Interaction, reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "math", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            seconds = s["math_seconds"]
            problem = make_math_problem(_rng)
            ends = int(time.time()) + seconds
            body = f"احسب الناتج:\n# {problem.text} = ؟\n-# المستوى: {problem.level}"
            await self._send_card(interaction, ctx, self._card(ctx, s, body, ends_at=ends))
            started = time.monotonic()

            def check(m: discord.Message):
                return (m.channel.id == interaction.channel_id and not m.author.bot
                        and parse_number(m.content) == problem.answer)

            msg = await self._wait_message(check, seconds)
            if not msg:
                await self._close(ctx, "لم يحلها أحد")
                return await self._announce(interaction.channel, self._timeout_embed(
                    ctx, f"**الحل:** {problem.text} = **{problem.answer}**"))
            elapsed = time.monotonic() - started
            await self._react(msg, "🧠")
            extra = await self._award(interaction.guild, msg.author, ctx)
            await self._close(ctx, f"فاز {msg.author.mention}")
            await self._announce(interaction.channel, self._win_embed(
                ctx, msg.author, f"{msg.author.mention} حلّها أولًا!",
                [("المسألة", f"`{problem.text} = {problem.answer}`"), ("الوقت", f"`{elapsed:.2f}` ثانية")],
                extra), reply_to=msg)
        finally:
            self._finish(ctx)

    # ================================================================== خمّن الرقم
    @game.command(name="guess", description="خمّن الرقم السري")
    @app_commands.describe(maximum="أعلى رقم ممكن", reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def guess(self, interaction: discord.Interaction, maximum: app_commands.Range[int, 10, 1000] = 100,
                    reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "guess", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            seconds, max_tries = s["guess_seconds"], s["guess_max_tries"]
            state = GuessState(_rng.randint(1, maximum), maximum, max_tries)
            ends = int(time.time()) + seconds

            def render() -> discord.Embed:
                span = state.high - state.low + 1
                body = (f"فكرت برقم بين **1** و **{maximum:,}**\n"
                        f"# {state.low:,}  ⟷  {state.high:,}\n"
                        f"{ui.progress_bar(maximum - span, maximum - 1, 14)}  النطاق يضيق!\n"
                        f"-# لكل عضو {max_tries} محاولات  ·  عدد التخمينات: {state.total}")
                return self._card(ctx, s, body, ends_at=ends)

            await self._send_card(interaction, ctx, render())
            dirty = asyncio.Event()

            async def updater():  # تحديث البطاقة بحد أقصى مرة كل 2.5 ثانية لتجنب حدود ديسكورد
                while True:
                    await dirty.wait()
                    dirty.clear()
                    await ui.safe_edit(ctx.message, embed=render())
                    await asyncio.sleep(2.5)

            ctx.helpers.append(asyncio.create_task(updater()))

            def check(m: discord.Message):
                return (m.channel.id == interaction.channel_id and not m.author.bot
                        and parse_number(m.content) is not None)

            reactions = {"higher": "⬆️", "lower": "⬇️", "blocked": "🚫", "out": "❔"}
            while True:
                msg = await self._wait_message(check, ends - time.time())
                if not msg:
                    await self._close(ctx, "انتهى الوقت")
                    return await self._announce(interaction.channel, self._timeout_embed(
                        ctx, f"**الرقم كان:** {state.secret}"))
                result = state.guess(msg.author.id, parse_number(msg.content))
                if result == "win":
                    break
                await self._react(msg, reactions[result])
                if result in ("higher", "lower"):
                    dirty.set()

            used = state.tries[msg.author.id]
            await self._react(msg, "🎯")
            extra = await self._award(interaction.guild, msg.author, ctx)
            await self._close(ctx, f"فاز {msg.author.mention}")
            await self._announce(interaction.channel, self._win_embed(
                ctx, msg.author, f"{msg.author.mention} أصاب الرقم!",
                [("الرقم", f"`{state.secret}`"), ("محاولاته", f"`{used}` من {max_tries}"),
                 ("مجموع التخمينات", f"`{state.total}`")], extra), reply_to=msg)
        finally:
            self._finish(ctx)

    # ================================================================== سؤال سريع
    @game.command(name="quiz", description="سؤال معلومات عامة — أول إجابة صحيحة تفوز")
    @app_commands.describe(reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def quiz(self, interaction: discord.Interaction, reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "quiz", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            seconds = s["quiz_seconds"]
            question, answer, wrong = _rng.choice(await self._question_pool(interaction.guild_id))
            options = quiz_options(answer, wrong, _rng)
            view = QuizView(options, answer, seconds)
            ends = int(time.time()) + seconds
            body = f"### {question}\n" + "\n".join(f"{QuizView.LETTERS[i]}  {o}" for i, o in enumerate(options))
            await self._send_card(interaction, ctx, self._card(ctx, s, body, ends_at=ends), view=view)
            await view.wait()
            view.reveal()
            stats = f"أجاب {len(view.attempted)} عضو"
            if not view.winner:
                await self._close(ctx, stats, view=view)
                return await self._announce(interaction.channel, self._timeout_embed(
                    ctx, f"**الإجابة الصحيحة:** {answer}"))
            extra = await self._award(interaction.guild, view.winner, ctx)
            await self._close(ctx, f"فاز {view.winner.mention}  ·  {stats}", view=view)
            await self._announce(interaction.channel, self._win_embed(
                ctx, view.winner, f"{view.winner.mention} أجاب صح أولًا!",
                [("الإجابة", f"**{answer}**"), ("الوقت", f"`{view.elapsed:.2f}` ثانية"),
                 ("حاولوا", f"`{len(view.attempted)}` عضو")], extra), reply_to=ctx.message)
        finally:
            self._finish(ctx)

    # ================================================================== الروليت
    @game.command(name="roulette", description="عجلة الروليت — العجلة تدور وتختار الفائز")
    @app_commands.describe(mode="نوع اللعبة: عجلة حظ مباشرة أو إقصاء على جولات",
                           join_seconds="مدة التسجيل بالثواني", reward="مكافأة مخصصة (لمنظمي الفعاليات)")
    async def roulette(self, interaction: discord.Interaction,
                       mode: Literal["عجلة الحظ", "إقصاء"] = "عجلة الحظ",
                       join_seconds: Optional[app_commands.Range[int, 15, 180]] = None,
                       reward: RewardOpt = None):
        ctx = await self._prepare(interaction, "roulette", reward)
        if not ctx:
            return
        try:
            s = await self.bot.db.get_settings(interaction.guild_id)
            await self._run_roulette(interaction, ctx, s, mode == "إقصاء",
                                     join_seconds or s["roulette_join_seconds"])
        finally:
            self._finish(ctx)

    def _players_block(self, players: list[discord.Member], numbered: bool = True) -> str:
        if not players:
            return "*لا أحد بعد — كن أول المنضمين!*"
        lines = [f"`{i:>2}` {m.mention}" if numbered else m.mention for i, m in enumerate(players, 1)]
        text = "\n".join(lines)
        if len(text) > 1000:
            text = " ".join(m.mention for m in players)[:1000]
        return text

    async def _run_roulette(self, interaction: discord.Interaction, ctx: GameContext, s: dict,
                            elimination: bool, join_seconds: int):
        min_players = s["roulette_min_players"]
        can_attach = interaction.app_permissions.attach_files
        lobby = RouletteLobby(self, ctx, timeout=join_seconds)
        end_at = int(time.time()) + join_seconds
        mode_line = ("🔥 **إقصاء:** كل جولة تختار العجلة لاعبًا يطرد منافسًا، والنهائي بين آخر اثنين"
                     if elimination else "🍀 **عجلة الحظ:** العجلة تدور مرة واحدة وتختار الفائز")

        def lobby_embed() -> discord.Embed:
            body = (f"{mode_line}\n\nاضغط **دخول** للمشاركة  ·  تبدأ العجلة {ui.countdown(end_at)}\n"
                    f"-# أقل عدد لاحتساب الجائزة: {min_players} لاعبين مؤهلين")
            e = self._card(ctx, s, body, footer="🟢 بانتظار المشاركين")
            e.add_field(name=f"👥 المشاركون  {len(lobby.players)}/{RouletteLobby.MAX}",
                        value=self._players_block(lobby.players), inline=False)
            if can_attach:
                e.set_image(url="attachment://wheel.png")
            return e

        async def refresh_image():
            png = await asyncio.to_thread(wheel.render_lobby, [m.display_name for m in lobby.players])
            await ui.safe_edit(ctx.message, embed=lobby_embed(),
                               attachments=[discord.File(io.BytesIO(png), "wheel.png")])

        lobby.render = lobby_embed
        lobby.refresh_image = refresh_image if can_attach else None
        file = None
        if can_attach:
            png = await asyncio.to_thread(wheel.render_lobby, [])
            file = discord.File(io.BytesIO(png), "wheel.png")
        await self._send_card(interaction, ctx, lobby_embed(), view=lobby, file=file)
        await lobby.wait()
        lobby.cancel_refresh()

        players = list(lobby.players)
        if lobby.cancelled_by:
            return await self._close(ctx, f"🚫 ألغاها {lobby.cancelled_by.mention}")
        if len(players) < 2:
            reason = "لم يشارك أحد" if not players else "شارك لاعب واحد فقط — يلزم لاعبان على الأقل"
            await self._close(ctx, f"🚫 أُلغيت: {reason}")
            return await self._announce(interaction.channel, ui.info(
                "🎡  أُلغيت الروليت", f"{reason}.\n-# جرّبوا مرة أخرى وادعوا أصدقاءكم!", ui.Colors.LOG))

        eligible = sum(1 for m in players if earning_block_reason(m, s) is None)
        total = len(players)
        if elimination:
            players = await self._elimination_rounds(interaction, ctx, s, players, can_attach)
            if players is None:
                return

        # الفائز يُحدد هنا بمنطق البوت فقط، والأنيميشن يعرض النتيجة نفسها
        names = [m.display_name for m in players]
        win_idx = wheel.choose_winner(len(players))
        angle = wheel.stop_angle(win_idx, len(players))
        winner = players[win_idx]

        spin_body = (f"{mode_line}\n\n🌀 **العجلة تدور...**\n\n"
                     + ("**النهائي:** " + " 🆚 ".join(m.mention for m in players) if elimination
                        else f"**المشاركون ({total}):**\n{self._players_block(players)}"))
        spin_embed = self._card(ctx, s, spin_body, footer="⏳ لحظات ويتوقف المؤشر...")
        spin_wait = 1.5
        files = []
        if can_attach:
            spin = await asyncio.to_thread(wheel.render_spin, names, win_idx, angle)
            spin_embed.set_image(url="attachment://spin.gif")
            files = [discord.File(io.BytesIO(spin.gif), "spin.gif")]
            spin_wait = (spin.duration_ms - 2500) / 1000 + 1.2
        msg = await self._present(interaction.channel, ctx, spin_embed, files)
        await asyncio.sleep(spin_wait)

        if eligible >= min_players:
            extra = await self._award(interaction.guild, winner, ctx)
        else:
            extra = (f"\n\n{ui.Icons.WARNING} لم تُحتسب الجائزة لأن عدد اللاعبين المؤهلين "
                     f"({eligible}) أقل من {min_players}.")
        result = ui.info(f"👑  {winner.display_name} فاز بالروليت!",
                         f"{winner.mention} {'صمد حتى النهاية' if elimination else 'اختارته العجلة'} من بين "
                         f"**{total}** لاعبين 🎉{extra}", ui.Colors.GOLD)
        result.set_thumbnail(url=winner.display_avatar.url)
        result.set_footer(text=f"🎡 الروليت  ·  {'إقصاء' if elimination else 'عجلة الحظ'}")
        files = []
        if can_attach:
            png = await asyncio.to_thread(wheel.render_result, names, win_idx, angle)
            result.set_image(url="attachment://result.png")
            files = [discord.File(io.BytesIO(png), "result.png")]
        if not await ui.safe_edit(msg, embed=result, attachments=files):
            await ui.safe_send(interaction.channel, embed=result, files=files)
        # رسالة قصيرة تنبّه الفائز (التعديل وحده لا يرسل إشعارًا)
        await ui.safe_send(interaction.channel, content=f"🎉 مبروك {winner.mention}!",
                           allowed_mentions=discord.AllowedMentions(users=[winner]))

    async def _present(self, channel, ctx: GameContext, embed: discord.Embed,
                       files: list[discord.File]) -> Optional[discord.Message]:
        """يعرض المرحلة التالية بتعديل رسالة اللعبة، أو يرسل رسالة جديدة إن تعذّر التعديل."""
        if ctx.message is not None:
            try:
                await ctx.message.edit(embed=embed, attachments=files, view=None)
                return ctx.message
            except discord.HTTPException:
                for f in files:
                    f.reset()
        ctx.message = await ui.safe_send(channel, embed=embed, files=files)
        return ctx.message

    async def _elimination_rounds(self, interaction, ctx: GameContext, s: dict,
                                  players: list[discord.Member], can_attach: bool):
        """جولات الإقصاء: العجلة تختار من يطرد، حتى يبقى لاعبان للنهائي."""
        channel = interaction.channel
        round_no = 0
        while len(players) > 2:
            round_no += 1
            await asyncio.sleep(1.5)
            names = [m.display_name for m in players]
            idx = wheel.choose_winner(len(players))
            angle = wheel.stop_angle(idx, len(players), spins=0)
            chooser = players[idx]
            others = [p for p in players if p.id != chooser.id]
            e = ui.info(f"🎡  الجولة {round_no}  ·  المتبقون {len(players)}",
                        f"العجلة اختارت {chooser.mention}!\nاختر من تطرده خلال **15** ثانية، وإلا ستخرج أنت 😈",
                        ui.Colors.GAME)
            files = []
            if can_attach:
                png = await asyncio.to_thread(wheel.render_result, names, idx, angle)
                e.set_image(url="attachment://round.png")
                files = [discord.File(io.BytesIO(png), "round.png")]
            if len(others) > 25:  # القائمة لا تتسع: الإقصاء عشوائي
                out, reason = _rng.choice(players), "🎲 العدد كبير، فاختارت العجلة عشوائيًا"
                msg = await ui.safe_send(channel, embed=e, files=files)
            else:
                view = KickView(chooser, others)
                ctx.views.append(view)
                msg = await ui.safe_send(channel, content=chooser.mention, embed=e, files=files, view=view)
                view.message = msg
                await view.wait()
                if view.choice == "self":
                    out, reason = chooser, "🏳️ انسحب بنفسه"
                elif view.choice:
                    out = next(p for p in others if p.id == view.choice)
                    reason = f"👢 طرده {chooser.mention}"
                else:
                    out, reason = chooser, "⌛ لم يختر في الوقت"
            players.remove(out)
            done = ui.info(f"❌  خرج {out.display_name}",
                           f"{reason}\n\n**المتبقون ({len(players)}):** " + " ".join(m.mention for m in players),
                           ui.Colors.ERROR)
            if files:
                done.set_image(url="attachment://round.png")
            await ui.safe_edit(msg, embed=done, view=None)
        await asyncio.sleep(1.5)
        ctx.message = None  # النهائي في رسالة جديدة أسفل الجولات
        return players

    # ================================================================== تحكم المنظمين
    @game.command(name="stop", description="إيقاف اللعبة القائمة في هذا الروم")
    @require("events")
    async def stop(self, interaction: discord.Interaction):
        ctx = self.active.get(interaction.channel_id)
        if not ctx:
            return await ui.reply(interaction, ui.error("لا توجد لعبة قائمة هنا."), ephemeral=True)
        if ctx.task and not ctx.task.done():
            ctx.task.cancel()
        self._finish(ctx)
        await self._close(ctx, f"⏹️ أوقفها {interaction.user.mention}")
        await ui.reply(interaction, ui.warning("تم إيقاف اللعبة",
                                               f"أوقف {interaction.user.mention} لعبة **{GAMES[ctx.game].name}**."))

    @game.command(name="add-word", description="إضافة كلمة لألعاب الكتابة والتفكيك")
    @require("events")
    async def add_word(self, interaction: discord.Interaction, word: app_commands.Range[str, 2, 30]):
        ok, msg = await add_custom_word(self.bot, interaction.guild_id, word, interaction.user.id)
        await ui.reply(interaction, ui.success("تمت إضافة الكلمة", msg) if ok else ui.error(msg), ephemeral=True)

    @game.command(name="add-question", description="إضافة سؤال لمسابقة الأسئلة")
    @app_commands.describe(question="السؤال", answer="الإجابة الصحيحة", wrong1="إجابة خاطئة 1",
                           wrong2="إجابة خاطئة 2", wrong3="إجابة خاطئة 3")
    @require("events")
    async def add_question(self, interaction: discord.Interaction, question: app_commands.Range[str, 5, 200],
                           answer: app_commands.Range[str, 1, 80], wrong1: app_commands.Range[str, 1, 80],
                           wrong2: app_commands.Range[str, 1, 80], wrong3: app_commands.Range[str, 1, 80]):
        ok, msg = await add_custom_question(self.bot, interaction.guild_id, question, answer,
                                            [wrong1, wrong2, wrong3], interaction.user.id)
        await ui.reply(interaction, ui.success("تمت إضافة السؤال", msg) if ok else ui.error(msg), ephemeral=True)

    @game.command(name="content", description="عرض الكلمات والأسئلة المضافة من الإدارة")
    @require("events")
    async def content(self, interaction: discord.Interaction):
        await ui.reply(interaction, await content_embed(self.bot, interaction.guild_id), ephemeral=True)

    @game.command(name="remove-content", description="حذف كلمة أو سؤال مضاف")
    @require("events")
    async def remove_content(self, interaction: discord.Interaction, content_id: int):
        ok = await self.bot.db.delete_custom(interaction.guild_id, content_id)
        await ui.reply(interaction, ui.success("تم الحذف", f"`#{content_id}`") if ok else ui.error("رقم غير موجود."),
                       ephemeral=True)

    @game.command(name="list", description="قائمة الألعاب ومكافآتها")
    async def list_games(self, interaction: discord.Interaction):
        s = await self.bot.db.get_settings(interaction.guild_id)
        e = ui.info(f"{ui.Icons.GAME}  مركز الألعاب",
                    "العب، اجمع النقاط، واصرفها في `/shop`!\n" + ui.LINE, ui.Colors.GAME)
        for key, g in GAMES.items():
            on = s.get(f"game_{key}_enabled", True)
            secs = f"  ·  ⏱️ {s[g.seconds_key]} ث" if g.seconds_key else ""
            status = "" if on else "  ·  🔴 **معطّلة**"
            e.add_field(name=f"{g.emoji}  {g.name}  —  /game {key}",
                        value=f"{g.about}\n🏆 {ui.money(s[f'reward_{key}'], s)}{secs}{status}", inline=False)
        cap = s["daily_earn_cap"]
        e.set_footer(text=f"الحد اليومي للكسب من الألعاب: {cap:,}" if cap else "لا يوجد حد يومي للكسب")
        await ui.reply(interaction, ui.brand(e, interaction.guild))


# ---------------------------------------------------------------------- المحتوى المخصص (مشترك مع اللوحة)
async def add_custom_word(bot, guild_id: int, word: str, user_id: int) -> tuple[bool, str]:
    word = " ".join(word.split())
    if not 2 <= len(word) <= 30:
        return False, "الكلمة يجب أن تكون بين 2 و 30 حرفًا."
    if has_invisible(word):
        return False, "الكلمة تحتوي محارف غير مرئية."
    existing = {normalize(r["content"]) for r in await bot.db.custom(guild_id, "word")} | {normalize(w) for w in WORDS}
    if normalize(word) in existing:
        return False, "هذه الكلمة موجودة مسبقًا."
    cid = await bot.db.add_custom(guild_id, "word", word, created_by=user_id)
    return True, f"`#{cid}` **{word}**"


async def add_custom_question(bot, guild_id: int, question: str, answer: str, wrong: list[str],
                              user_id: int) -> tuple[bool, str]:
    question, answer = question.strip(), answer.strip()
    wrong = [w.strip() for w in wrong]
    if not 5 <= len(question) <= 200 or not answer or any(not w for w in wrong):
        return False, "السؤال والإجابات مطلوبة (السؤال من 5 إلى 200 حرف)."
    if any(len(x) > 80 for x in wrong + [answer]):
        return False, "كل إجابة يجب ألا تتجاوز 80 حرفًا."
    if len({normalize(x) for x in wrong + [answer]}) < 4:
        return False, "الإجابات الأربع يجب أن تكون مختلفة."
    cid = await bot.db.add_custom(guild_id, "question", question, answer,
                                  json.dumps(wrong, ensure_ascii=False), user_id)
    return True, f"`#{cid}` {question}\n✅ {answer}"


async def content_embed(bot, guild_id: int) -> discord.Embed:
    words = await bot.db.custom(guild_id, "word")
    questions = await bot.db.custom(guild_id, "question")
    e = ui.info("📚  المحتوى المخصص", f"الافتراضي: **{len(WORDS)}** كلمة و **{len(QUESTIONS)}** سؤال", ui.Colors.INFO)
    e.add_field(name=f"الكلمات ({len(words)})",
                value=("، ".join(f"`#{w['id']}` {w['content']}" for w in words) or "—")[:1024], inline=False)
    e.add_field(name=f"الأسئلة ({len(questions)})",
                value=("\n".join(f"`#{q['id']}` {q['content']}" for q in questions) or "—")[:1024], inline=False)
    e.set_footer(text="للحذف: /game remove-content")
    return e


# ---------------------------------------------------------------------- واجهات الأزرار
class QuizView(ui.BaseView):
    LETTERS = ["🇦", "🇧", "🇨", "🇩"]

    def __init__(self, options: list[str], answer: str, seconds: int):
        super().__init__(timeout=seconds)
        self.answer = answer
        self.winner: Optional[discord.Member] = None
        self.attempted: set[int] = set()
        self.started = time.monotonic()
        self.elapsed = 0.0
        self.lock = asyncio.Lock()
        for i, opt in enumerate(options):
            btn = discord.ui.Button(label=opt[:80], emoji=self.LETTERS[i], style=discord.ButtonStyle.secondary)
            btn.callback = self._make_callback(opt)
            self.add_item(btn)

    async def on_timeout(self):
        self.stop()  # الكشف والتعطيل يتمان في الأمر نفسه

    def reveal(self):
        for child in self.children:
            child.disabled = True
            if isinstance(child, discord.ui.Button):
                child.style = (discord.ButtonStyle.success if child.label == self.answer[:80]
                               else discord.ButtonStyle.secondary)

    def _make_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            async with self.lock:  # الضغطات المتزامنة تُعالج واحدة تلو الأخرى: فائز واحد فقط
                if self.winner or self.is_finished():
                    return await interaction.response.send_message(embed=ui.error("انتهى السؤال.", "متأخر قليلًا"),
                                                                   ephemeral=True)
                if interaction.user.bot or interaction.user.id in self.attempted:
                    return await interaction.response.send_message(
                        embed=ui.error("لكل عضو محاولة واحدة فقط.", "استخدمت محاولتك"), ephemeral=True)
                self.attempted.add(interaction.user.id)
                if option == self.answer:
                    self.winner = interaction.user
                    self.elapsed = time.monotonic() - self.started
                    self.stop()
                    await interaction.response.send_message(embed=ui.success("إجابة صحيحة! 🎉",
                                                                             f"أجبت في **{self.elapsed:.2f}** ثانية"),
                                                            ephemeral=True)
                else:
                    await interaction.response.send_message(
                        embed=ui.error("حظًا أوفر في السؤال القادم 😅", "إجابة خاطئة"), ephemeral=True)
        return callback


class RouletteLobby(ui.BaseView):
    MAX = 50

    def __init__(self, cog: Games, ctx: GameContext, timeout: float):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.players: list[discord.Member] = []
        self.cancelled_by: Optional[discord.Member] = None
        self.render = None
        self.refresh_image = None
        self._img_task: Optional[asyncio.Task] = None
        self._dirty = False

    async def on_timeout(self):
        self.stop()

    # تحديث صورة العجلة مؤجَّل ومجمَّع: كثرة المنضمين لا تسبب رفع صور متتالية
    def schedule_image(self):
        if not self.refresh_image:
            return
        if self._img_task and not self._img_task.done():
            self._dirty = True
            return
        self._img_task = asyncio.create_task(self._image_loop())

    async def _image_loop(self):
        while True:
            self._dirty = False
            await asyncio.sleep(2)
            if self.is_finished():
                return
            try:
                await self.refresh_image()
            except Exception:  # noqa: BLE001 - الصورة تحسين بصري ولا يجب أن توقف اللعبة
                ui.log.exception("Lobby image refresh failed")
            if not self._dirty:
                return

    def cancel_refresh(self):
        if self._img_task and not self._img_task.done():
            self._img_task.cancel()

    async def _is_host(self, interaction: discord.Interaction) -> bool:
        return (interaction.user.id == self.ctx.starter.id
                or await has_category(self.cog.bot.db, interaction.user, "events"))

    @discord.ui.button(label="دخول", style=discord.ButtonStyle.success, emoji="🎟️")
    async def join(self, interaction: discord.Interaction, _b: discord.ui.Button):
        if interaction.user.bot:
            return await interaction.response.defer()
        if any(p.id == interaction.user.id for p in self.players):
            return await interaction.response.send_message(embed=ui.info("✅ أنت مسجّل بالفعل",
                                                                         color=ui.Colors.SUCCESS), ephemeral=True)
        if len(self.players) >= self.MAX:
            return await interaction.response.send_message(embed=ui.error("اكتمل العدد.", "الروليت ممتلئة"),
                                                           ephemeral=True)
        self.players.append(interaction.user)
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.schedule_image()

    @discord.ui.button(label="خروج", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, _b: discord.ui.Button):
        before = len(self.players)
        self.players = [p for p in self.players if p.id != interaction.user.id]
        if len(self.players) == before:
            return await interaction.response.send_message(embed=ui.error("أنت غير مسجّل في هذه الجولة."),
                                                           ephemeral=True)
        await interaction.response.edit_message(embed=self.render(), view=self)
        self.schedule_image()

    @discord.ui.button(label="ابدأ الآن", style=discord.ButtonStyle.primary, emoji="▶️", row=1)
    async def start_now(self, interaction: discord.Interaction, _b: discord.ui.Button):
        if not await self._is_host(interaction):
            return await interaction.response.send_message(
                embed=ui.error("البدء المبكر لصاحب اللعبة أو منظمي الفعاليات فقط."), ephemeral=True)
        if len(self.players) < 2:
            return await interaction.response.send_message(embed=ui.error("يلزم لاعبان على الأقل للبدء."),
                                                           ephemeral=True)
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="إلغاء", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def cancel(self, interaction: discord.Interaction, _b: discord.ui.Button):
        if not await self._is_host(interaction):
            return await interaction.response.send_message(
                embed=ui.error("الإلغاء لصاحب اللعبة أو منظمي الفعاليات فقط."), ephemeral=True)
        self.cancelled_by = interaction.user
        await interaction.response.defer()
        self.stop()


class KickView(ui.BaseView):
    deny_message = "ليس دورك الآن 😄"

    def __init__(self, chooser: discord.Member, others: list[discord.Member]):
        super().__init__(owner_id=chooser.id, timeout=15)
        self.choice: Optional[int | str] = None
        select = discord.ui.Select(
            placeholder="👢 اختر من تطرد...",
            options=[discord.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in others],
        )
        select.callback = self._picked
        self.add_item(select)

    async def on_timeout(self):
        self.stop()

    async def _picked(self, interaction: discord.Interaction):
        if self.choice is None:
            self.choice = int(interaction.data["values"][0])
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="انسحاب", style=discord.ButtonStyle.danger, emoji="🏳️", row=1)
    async def withdraw(self, interaction: discord.Interaction, _b: discord.ui.Button):
        if self.choice is None:
            self.choice = "self"
        await interaction.response.defer()
        self.stop()


async def setup(bot):
    await bot.add_cog(Games(bot))

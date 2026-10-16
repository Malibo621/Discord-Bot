"""نظام الصلاحيات: كل فئة أوامر يمكن ربطها برتب محددة من الإدارة."""
from __future__ import annotations

import discord
from discord import app_commands

from .settings_schema import PERMISSION_CATEGORIES


class NoPermission(app_commands.CheckFailure):
    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def is_admin(member: discord.abc.User) -> bool:
    """المسؤول = صلاحية Administrator أو مالك السيرفر."""
    return isinstance(member, discord.Member) and (
        member.guild_permissions.administrator or member.id == member.guild.owner_id)


async def has_category(db, member: discord.Member, category: str) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if is_admin(member):
        return True
    roles = await db.perm_roles(member.guild.id, category)
    if roles:
        return any(r.id in roles for r in member.roles)
    fallback = PERMISSION_CATEGORIES[category][1]
    return getattr(member.guild_permissions, fallback, False)


def require(category: str):
    """مُزخرف للأوامر: يسمح فقط لمن يملك رتبة الفئة (أو الصلاحية الافتراضية إن لم تُحدد رتب)."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            raise app_commands.NoPrivateMessage()
        if await has_category(interaction.client.db, interaction.user, category):
            return True
        raise NoPermission(category)

    return app_commands.check(predicate)


async def allowed(db, member: discord.abc.User, perm: str) -> bool:
    """perm = "admin" أو اسم فئة من PERMISSION_CATEGORIES."""
    if perm == "admin":
        return is_admin(member)
    return await has_category(db, member, perm)


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if is_admin(interaction.user):
            return True
        raise NoPermission("admin")

    return app_commands.check(predicate)

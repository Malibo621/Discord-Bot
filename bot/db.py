"""طبقة قاعدة البيانات (SQLite) — كل العمليات المالية ذرّية لمنع التلاعب والأرصدة السالبة."""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import aiosqlite

from .settings_schema import SETTINGS_BY_KEY

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
    total_earned INTEGER NOT NULL DEFAULT 0,
    games_won INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    other_id INTEGER,
    amount INTEGER NOT NULL,
    kind TEXT NOT NULL,
    note TEXT,
    balance_after INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions (guild_id, user_id, id DESC);
CREATE TABLE IF NOT EXISTS daily_stats (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    earned INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE IF NOT EXISTS shop_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    price INTEGER NOT NULL CHECK (price >= 0),
    kind TEXT NOT NULL,              -- role | custom
    role_id INTEGER,
    stock INTEGER NOT NULL DEFAULT -1, -- -1 = غير محدود
    duration_hours INTEGER NOT NULL DEFAULT 0, -- 0 = دائم
    one_per_user INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    price INTEGER NOT NULL,
    status TEXT NOT NULL,      -- delivered | pending | expired | refunded
    expires_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases (guild_id, user_id);
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    mod_id INTEGER NOT NULL,
    reason TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS command_perms (
    guild_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, category, role_id)
);
CREATE TABLE IF NOT EXISTS custom_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,   -- word | question
    content TEXT NOT NULL,
    answer TEXT,
    options TEXT,
    created_by INTEGER,
    created_at INTEGER NOT NULL
);
-- مفتاح فريد لكل جائزة لعبة: يمنع صرف الجائزة مرتين بسبب الضغط المتكرر أو التفاعلات المتزامنة
CREATE TABLE IF NOT EXISTS reward_claims (
    claim_key TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
"""


class InsufficientFunds(Exception):
    pass


class LimitReached(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def now() -> int:
    return int(time.time())


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()
        self._settings_cache: dict[int, dict[str, Any]] = {}

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.executescript(SCHEMA)

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    @asynccontextmanager
    async def tx(self):
        """معاملة ذرّية: إما تنجح كل العمليات أو تُلغى كلها."""
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            else:
                await self.conn.execute("COMMIT")

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def execute(self, sql: str, params: tuple = ()) -> int:
        async with self._lock:
            cur = await self.conn.execute(sql, params)
            return cur.lastrowid

    # ------------------------------------------------------------------ الإعدادات
    async def get_settings(self, guild_id: int) -> dict[str, Any]:
        if guild_id in self._settings_cache:
            return self._settings_cache[guild_id]
        values = {k: s.default for k, s in SETTINGS_BY_KEY.items()}
        for row in await self.fetchall("SELECT key, value FROM settings WHERE guild_id=?", (guild_id,)):
            s = SETTINGS_BY_KEY.get(row["key"])
            if not s:
                continue
            raw = row["value"]
            if s.kind in ("int", "channel"):
                values[s.key] = int(raw)
            elif s.kind == "bool":
                values[s.key] = raw == "1"
            else:
                values[s.key] = raw
        self._settings_cache[guild_id] = values
        return values

    async def get(self, guild_id: int, key: str) -> Any:
        return (await self.get_settings(guild_id))[key]

    async def set_setting(self, guild_id: int, key: str, value: Any) -> None:
        if isinstance(value, bool):
            raw = "1" if value else "0"
        else:
            raw = str(value)
        await self.execute(
            "INSERT INTO settings (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
            (guild_id, key, raw),
        )
        self._settings_cache.pop(guild_id, None)

    async def reset_setting(self, guild_id: int, key: str) -> None:
        await self.execute("DELETE FROM settings WHERE guild_id=? AND key=?", (guild_id, key))
        self._settings_cache.pop(guild_id, None)

    # ------------------------------------------------------------------ الصلاحيات
    async def perm_roles(self, guild_id: int, category: str) -> list[int]:
        rows = await self.fetchall(
            "SELECT role_id FROM command_perms WHERE guild_id=? AND category=?", (guild_id, category)
        )
        return [r["role_id"] for r in rows]

    async def add_perm_role(self, guild_id: int, category: str, role_id: int) -> None:
        await self.execute(
            "INSERT OR IGNORE INTO command_perms (guild_id, category, role_id) VALUES (?, ?, ?)",
            (guild_id, category, role_id),
        )

    async def set_perm_roles(self, guild_id: int, category: str, role_ids: list[int]) -> None:
        async with self.tx() as conn:
            await conn.execute("DELETE FROM command_perms WHERE guild_id=? AND category=?", (guild_id, category))
            await conn.executemany(
                "INSERT OR IGNORE INTO command_perms (guild_id, category, role_id) VALUES (?, ?, ?)",
                [(guild_id, category, r) for r in role_ids])

    async def remove_perm_role(self, guild_id: int, category: str, role_id: int) -> None:
        await self.execute(
            "DELETE FROM command_perms WHERE guild_id=? AND category=? AND role_id=?",
            (guild_id, category, role_id),
        )

    # ------------------------------------------------------------------ المحافظ
    async def _ensure_wallet(self, conn, guild_id: int, user_id: int) -> None:
        await conn.execute(
            "INSERT OR IGNORE INTO wallets (guild_id, user_id) VALUES (?, ?)", (guild_id, user_id)
        )

    async def _balance(self, conn, guild_id: int, user_id: int) -> int:
        async with conn.execute(
            "SELECT balance FROM wallets WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        ) as cur:
            row = await cur.fetchone()
        return row["balance"] if row else 0

    async def _log(self, conn, guild_id, user_id, amount, kind, note=None, other_id=None, balance_after=None):
        await conn.execute(
            "INSERT INTO transactions (guild_id, user_id, other_id, amount, kind, note, balance_after, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, other_id, amount, kind, note, balance_after, now()),
        )

    async def wallet(self, guild_id: int, user_id: int) -> dict:
        row = await self.fetchone(
            "SELECT balance, total_earned, games_won FROM wallets WHERE guild_id=? AND user_id=?",
            (guild_id, user_id),
        )
        data = dict(row) if row else {"balance": 0, "total_earned": 0, "games_won": 0}
        rank_row = await self.fetchone(
            "SELECT COUNT(*) + 1 AS r FROM wallets WHERE guild_id=? AND balance > ?",
            (guild_id, data["balance"]),
        )
        data["rank"] = rank_row["r"]
        stats = await self.fetchone(
            "SELECT earned, sent FROM daily_stats WHERE guild_id=? AND user_id=? AND day=?",
            (guild_id, user_id, today()),
        )
        data["earned_today"] = stats["earned"] if stats else 0
        data["sent_today"] = stats["sent"] if stats else 0
        return data

    async def balance(self, guild_id: int, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT balance FROM wallets WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        return row["balance"] if row else 0

    async def admin_adjust(self, guild_id: int, user_id: int, amount: int, admin_id: int, reason: str) -> int:
        """إضافة (موجب) أو خصم (سالب) من الإدارة. الخصم لا ينزل الرصيد تحت الصفر."""
        async with self.tx() as conn:
            await self._ensure_wallet(conn, guild_id, user_id)
            current = await self._balance(conn, guild_id, user_id)
            if amount < 0:
                amount = -min(current, -amount)
            new = current + amount
            await conn.execute(
                "UPDATE wallets SET balance=? WHERE guild_id=? AND user_id=?", (new, guild_id, user_id)
            )
            await self._log(conn, guild_id, user_id, amount, "admin_add" if amount >= 0 else "admin_remove",
                            reason, admin_id, new)
            return new

    async def admin_set(self, guild_id: int, user_id: int, value: int, admin_id: int, reason: str) -> int:
        async with self.tx() as conn:
            await self._ensure_wallet(conn, guild_id, user_id)
            current = await self._balance(conn, guild_id, user_id)
            await conn.execute(
                "UPDATE wallets SET balance=? WHERE guild_id=? AND user_id=?", (value, guild_id, user_id)
            )
            await self._log(conn, guild_id, user_id, value - current, "admin_set", reason, admin_id, value)
            return value

    async def game_reward(self, guild_id: int, user_id: int, amount: int, game: str, cap: int,
                          claim_key: Optional[str] = None) -> Optional[int]:
        """يمنح مكافأة لعبة مع احترام الحد اليومي. يرجع المبلغ الممنوح فعليًا.

        claim_key: معرّف فريد للجولة؛ إن سبق صرفه يرجع None دون أي تغيير (idempotent).
        """
        async with self.tx() as conn:
            if claim_key is not None:
                cur = await conn.execute(
                    "INSERT OR IGNORE INTO reward_claims (claim_key, guild_id, user_id, amount, created_at) "
                    "VALUES (?, ?, ?, ?, ?)", (claim_key, guild_id, user_id, amount, now()))
                if cur.rowcount == 0:
                    return None
            await self._ensure_wallet(conn, guild_id, user_id)
            day = today()
            await conn.execute(
                "INSERT OR IGNORE INTO daily_stats (guild_id, user_id, day) VALUES (?, ?, ?)",
                (guild_id, user_id, day),
            )
            async with conn.execute(
                "SELECT earned FROM daily_stats WHERE guild_id=? AND user_id=? AND day=?",
                (guild_id, user_id, day),
            ) as cur:
                earned = (await cur.fetchone())["earned"]
            granted = max(0, min(amount, cap - earned)) if cap > 0 else amount
            await conn.execute(
                "UPDATE wallets SET balance=balance+?, total_earned=total_earned+?, games_won=games_won+1 "
                "WHERE guild_id=? AND user_id=?",
                (granted, granted, guild_id, user_id),
            )
            if granted:
                await conn.execute(
                    "UPDATE daily_stats SET earned=earned+? WHERE guild_id=? AND user_id=? AND day=?",
                    (granted, guild_id, user_id, day),
                )
                new = await self._balance(conn, guild_id, user_id)
                await self._log(conn, guild_id, user_id, granted, "game", game, None, new)
            return granted

    async def transfer(self, guild_id: int, sender: int, receiver: int, amount: int,
                       tax_percent: int, daily_limit: int) -> tuple[int, int]:
        """تحويل ذرّي. يرجع (المبلغ المستلم، الضريبة)."""
        if amount <= 0 or sender == receiver:
            raise ValueError("invalid transfer")
        async with self.tx() as conn:
            await self._ensure_wallet(conn, guild_id, sender)
            await self._ensure_wallet(conn, guild_id, receiver)
            day = today()
            await conn.execute(
                "INSERT OR IGNORE INTO daily_stats (guild_id, user_id, day) VALUES (?, ?, ?)",
                (guild_id, sender, day),
            )
            async with conn.execute(
                "SELECT sent FROM daily_stats WHERE guild_id=? AND user_id=? AND day=?", (guild_id, sender, day)
            ) as cur:
                sent = (await cur.fetchone())["sent"]
            if daily_limit > 0 and sent + amount > daily_limit:
                raise LimitReached(f"تجاوزت الحد اليومي للتحويل. المتبقي لك اليوم: **{max(0, daily_limit - sent):,}**")
            cur = await conn.execute(
                "UPDATE wallets SET balance=balance-? WHERE guild_id=? AND user_id=? AND balance>=?",
                (amount, guild_id, sender, amount),
            )
            if cur.rowcount == 0:
                raise InsufficientFunds()
            tax = amount * tax_percent // 100
            received = amount - tax
            await conn.execute(
                "UPDATE wallets SET balance=balance+? WHERE guild_id=? AND user_id=?",
                (received, guild_id, receiver),
            )
            await conn.execute(
                "UPDATE daily_stats SET sent=sent+? WHERE guild_id=? AND user_id=? AND day=?",
                (amount, guild_id, sender, day),
            )
            s_bal = await self._balance(conn, guild_id, sender)
            r_bal = await self._balance(conn, guild_id, receiver)
            note = f"ضريبة {tax}" if tax else None
            await self._log(conn, guild_id, sender, -amount, "transfer_out", note, receiver, s_bal)
            await self._log(conn, guild_id, receiver, received, "transfer_in", note, sender, r_bal)
            return received, tax

    async def leaderboard(self, guild_id: int, limit: int = 10, offset: int = 0) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT user_id, balance, total_earned, games_won FROM wallets "
            "WHERE guild_id=? AND balance > 0 ORDER BY balance DESC, user_id LIMIT ? OFFSET ?",
            (guild_id, limit, offset),
        )

    async def leaderboard_count(self, guild_id: int) -> int:
        row = await self.fetchone("SELECT COUNT(*) AS c FROM wallets WHERE guild_id=? AND balance > 0", (guild_id,))
        return row["c"]

    async def transactions(self, guild_id: int, user_id: Optional[int], limit: int = 10, offset: int = 0):
        if user_id:
            return await self.fetchall(
                "SELECT * FROM transactions WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (guild_id, user_id, limit, offset),
            )
        return await self.fetchall(
            "SELECT * FROM transactions WHERE guild_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (guild_id, limit, offset),
        )

    # ------------------------------------------------------------------ المتجر
    async def shop_items(self, guild_id: int, include_inactive: bool = False):
        sql = "SELECT * FROM shop_items WHERE guild_id=?"
        if not include_inactive:
            sql += " AND active=1"
        return await self.fetchall(sql + " ORDER BY price, id", (guild_id,))

    async def shop_item(self, guild_id: int, item_id: int):
        return await self.fetchone("SELECT * FROM shop_items WHERE guild_id=? AND id=?", (guild_id, item_id))

    async def add_shop_item(self, guild_id, name, description, price, kind, role_id, stock, duration_hours,
                            one_per_user) -> int:
        return await self.execute(
            "INSERT INTO shop_items (guild_id, name, description, price, kind, role_id, stock, duration_hours, "
            "one_per_user, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, name, description, price, kind, role_id, stock, duration_hours, int(one_per_user), now()),
        )

    async def update_shop_item(self, guild_id: int, item_id: int, **fields) -> None:
        allowed = {"name", "description", "price", "stock", "duration_hours", "one_per_user", "active"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        await self.execute(
            f"UPDATE shop_items SET {sets} WHERE guild_id=? AND id=?", (*fields.values(), guild_id, item_id)
        )

    async def purchase(self, guild_id: int, user_id: int, item_id: int) -> tuple[int, int]:
        """شراء ذرّي: يتحقق من الرصيد والمخزون والتكرار ثم يخصم. يرجع (رقم الطلب، الرصيد الجديد)."""
        async with self.tx() as conn:
            async with conn.execute(
                "SELECT * FROM shop_items WHERE guild_id=? AND id=? AND active=1", (guild_id, item_id)
            ) as cur:
                item = await cur.fetchone()
            if not item:
                raise LimitReached("هذا المنتج غير متوفر.")
            if item["stock"] == 0:
                raise LimitReached("نفدت الكمية من هذا المنتج.")
            if item["one_per_user"]:
                async with conn.execute(
                    "SELECT 1 FROM purchases WHERE guild_id=? AND user_id=? AND item_id=? "
                    "AND status IN ('delivered','pending')",
                    (guild_id, user_id, item_id),
                ) as cur:
                    if await cur.fetchone():
                        raise LimitReached("تملك هذا المنتج مسبقًا.")
            await self._ensure_wallet(conn, guild_id, user_id)
            cur = await conn.execute(
                "UPDATE wallets SET balance=balance-? WHERE guild_id=? AND user_id=? AND balance>=?",
                (item["price"], guild_id, user_id, item["price"]),
            )
            if cur.rowcount == 0:
                raise InsufficientFunds()
            if item["stock"] > 0:
                await conn.execute("UPDATE shop_items SET stock=stock-1 WHERE id=?", (item_id,))
            expires = now() + item["duration_hours"] * 3600 if item["duration_hours"] else None
            status = "delivered" if item["kind"] == "role" else "pending"
            cur = await conn.execute(
                "INSERT INTO purchases (guild_id, user_id, item_id, item_name, price, status, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, item_id, item["name"], item["price"], status, expires, now()),
            )
            purchase_id = cur.lastrowid
            new = await self._balance(conn, guild_id, user_id)
            await self._log(conn, guild_id, user_id, -item["price"], "purchase",
                            f"#{purchase_id} {item['name']}", None, new)
            return purchase_id, new

    async def refund(self, guild_id: int, purchase_id: int, admin_id: int) -> Optional[aiosqlite.Row]:
        async with self.tx() as conn:
            async with conn.execute(
                "SELECT * FROM purchases WHERE guild_id=? AND id=? AND status IN ('pending','delivered')",
                (guild_id, purchase_id),
            ) as cur:
                p = await cur.fetchone()
            if not p:
                return None
            await conn.execute("UPDATE purchases SET status='refunded' WHERE id=?", (purchase_id,))
            await self._ensure_wallet(conn, guild_id, p["user_id"])
            await conn.execute(
                "UPDATE wallets SET balance=balance+? WHERE guild_id=? AND user_id=?",
                (p["price"], guild_id, p["user_id"]),
            )
            await conn.execute(
                "UPDATE shop_items SET stock=stock+1 WHERE id=? AND stock>=0", (p["item_id"],)
            )
            new = await self._balance(conn, guild_id, p["user_id"])
            await self._log(conn, guild_id, p["user_id"], p["price"], "refund",
                            f"#{purchase_id} {p['item_name']}", admin_id, new)
            return p

    async def purchase_row(self, guild_id: int, purchase_id: int):
        return await self.fetchone("SELECT * FROM purchases WHERE guild_id=? AND id=?", (guild_id, purchase_id))

    async def set_purchase_status(self, purchase_id: int, status: str) -> None:
        await self.execute("UPDATE purchases SET status=? WHERE id=?", (status, purchase_id))

    async def mark_delivered(self, guild_id: int, purchase_id: int) -> bool:
        """تسليم ذرّي: ينجح مرة واحدة فقط حتى لو ضغط مسؤولان في نفس اللحظة."""
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE purchases SET status='delivered' WHERE guild_id=? AND id=? AND status='pending'",
                (guild_id, purchase_id))
            return cur.rowcount > 0

    async def user_purchases(self, guild_id: int, user_id: int):
        return await self.fetchall(
            "SELECT * FROM purchases WHERE guild_id=? AND user_id=? ORDER BY id DESC LIMIT 25",
            (guild_id, user_id),
        )

    async def pending_purchases(self, guild_id: int):
        return await self.fetchall(
            "SELECT * FROM purchases WHERE guild_id=? AND status='pending' ORDER BY id LIMIT 25", (guild_id,)
        )

    async def expired_role_purchases(self):
        return await self.fetchall(
            "SELECT p.*, i.role_id FROM purchases p JOIN shop_items i ON i.id = p.item_id "
            "WHERE p.status='delivered' AND p.expires_at IS NOT NULL AND p.expires_at <= ? AND i.kind='role'",
            (now(),),
        )

    # ------------------------------------------------------------------ التحذيرات
    async def add_warning(self, guild_id: int, user_id: int, mod_id: int, reason: str) -> int:
        await self.execute(
            "INSERT INTO warnings (guild_id, user_id, mod_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, user_id, mod_id, reason, now()),
        )
        row = await self.fetchone(
            "SELECT COUNT(*) AS c FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        return row["c"]

    async def warnings(self, guild_id: int, user_id: int):
        return await self.fetchall(
            "SELECT * FROM warnings WHERE guild_id=? AND user_id=? ORDER BY id DESC", (guild_id, user_id)
        )

    async def delete_warning(self, guild_id: int, warn_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute("DELETE FROM warnings WHERE guild_id=? AND id=?", (guild_id, warn_id))
            return cur.rowcount > 0

    async def clear_warnings(self, guild_id: int, user_id: int) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM warnings WHERE guild_id=? AND user_id=?", (guild_id, user_id)
            )
            return cur.rowcount

    # ------------------------------------------------------------------ محتوى الألعاب المخصص
    async def add_custom(self, guild_id, kind, content, answer=None, options=None, created_by=None) -> int:
        return await self.execute(
            "INSERT INTO custom_content (guild_id, kind, content, answer, options, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (guild_id, kind, content, answer, options, created_by, now()),
        )

    async def custom(self, guild_id: int, kind: str):
        return await self.fetchall(
            "SELECT * FROM custom_content WHERE guild_id=? AND kind=?", (guild_id, kind)
        )

    async def delete_custom(self, guild_id: int, content_id: int) -> bool:
        async with self._lock:
            cur = await self.conn.execute(
                "DELETE FROM custom_content WHERE guild_id=? AND id=?", (guild_id, content_id)
            )
            return cur.rowcount > 0

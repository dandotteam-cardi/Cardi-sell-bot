from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import asynccontextmanager

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  user_id    INTEGER PRIMARY KEY,
  username   TEXT,
  full_name  TEXT,
  balance    INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
  banned     INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS transactions(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL,
  kind          TEXT NOT NULL,
  amount        INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id, id DESC);
CREATE TABLE IF NOT EXISTS plans(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  duration_days INTEGER NOT NULL,
  price         INTEGER NOT NULL,
  active        INTEGER NOT NULL DEFAULT 1,
  sort_order    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS receipts(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL,
  amount     INTEGER NOT NULL,
  status     TEXT NOT NULL DEFAULT 'pending',
  admin_msgs TEXT NOT NULL DEFAULT '[]',
  decided_by INTEGER,
  created_at INTEGER NOT NULL,
  decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS services(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL,
  plan_id       INTEGER,
  plan_name     TEXT NOT NULL,
  price         INTEGER NOT NULL,
  duration_days INTEGER NOT NULL,
  status        TEXT NOT NULL,
  install_step  TEXT,
  install_data  TEXT NOT NULL DEFAULT '{}',
  bot_token     TEXT,
  bot_username  TEXT,
  bot_name      TEXT,
  created_at    INTEGER NOT NULL,
  start_at      INTEGER NOT NULL,
  end_at        INTEGER NOT NULL,
  reminded      INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_services_token ON services(bot_token) WHERE bot_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_services_user ON services(user_id, id DESC);
CREATE TABLE IF NOT EXISTS settings(
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


def now() -> int:
    return int(time.time())


def _d(row: aiosqlite.Row | None) -> dict | None:
    return dict(row) if row is not None else None


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self._c: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self._c = await aiosqlite.connect(self.path, isolation_level=None)
        self._c.row_factory = aiosqlite.Row
        await self._c.execute("PRAGMA journal_mode=WAL")
        await self._c.executescript(SCHEMA)

    async def close(self) -> None:
        if self._c is not None:
            await self._c.close()
            self._c = None

    @asynccontextmanager
    async def tx(self):
        """تراکنش نوشتن؛ همه‌ی عملیات مالی داخل همین قفل و یک تراکنش انجام می‌شود."""
        async with self._lock:
            await self._c.execute("BEGIN IMMEDIATE")
            try:
                yield self._c
            except BaseException:
                await self._c.execute("ROLLBACK")
                raise
            else:
                await self._c.execute("COMMIT")

    async def _one(self, sql: str, args: tuple = ()) -> dict | None:
        async with self._lock:
            cur = await self._c.execute(sql, args)
            return _d(await cur.fetchone())

    async def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        async with self._lock:
            cur = await self._c.execute(sql, args)
            return [dict(r) for r in await cur.fetchall()]

    async def _exec(self, sql: str, args: tuple = ()) -> int:
        async with self.tx() as c:
            cur = await c.execute(sql, args)
            return cur.rowcount

    # ------------------------------------------------------------ users
    async def upsert_user(self, user_id: int, username: str | None, full_name: str) -> tuple[dict, bool]:
        async with self.tx() as c:
            cur = await c.execute(
                "INSERT OR IGNORE INTO users(user_id, username, full_name, created_at) VALUES(?,?,?,?)",
                (user_id, username, full_name, now()),
            )
            created = cur.rowcount == 1
            if not created:
                await c.execute(
                    "UPDATE users SET username=?, full_name=? "
                    "WHERE user_id=? AND (username IS NOT ? OR full_name IS NOT ?)",
                    (username, full_name, user_id, username, full_name),
                )
            cur = await c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
            return dict(await cur.fetchone()), created

    async def get_user(self, user_id: int) -> dict | None:
        return await self._one("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def find_user(self, raw: str) -> dict | None:
        raw = raw.strip()
        if raw.isdigit():
            return await self.get_user(int(raw))
        return await self._one("SELECT * FROM users WHERE lower(username)=lower(?)", (raw.lstrip("@"),))

    async def set_banned(self, user_id: int, banned: bool) -> None:
        await self._exec("UPDATE users SET banned=? WHERE user_id=?", (int(banned), user_id))

    # ----------------------------------------------------------- wallet
    async def adjust_balance(self, user_id: int, delta: int, kind: str, description: str = "") -> int | None:
        """تغییر موجودی همراه با ثبت تراکنش. اگر موجودی منفی شود None برمی‌گردد."""
        async with self.tx() as c:
            return await self._adjust(c, user_id, delta, kind, description)

    @staticmethod
    async def _adjust(c, user_id: int, delta: int, kind: str, description: str) -> int | None:
        cur = await c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        new = row["balance"] + delta
        if new < 0:
            return None
        await c.execute("UPDATE users SET balance=? WHERE user_id=?", (new, user_id))
        await c.execute(
            "INSERT INTO transactions(user_id, kind, amount, balance_after, description, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, kind, delta, new, description, now()),
        )
        return new

    async def list_transactions(self, user_id: int, limit: int = 15) -> list[dict]:
        return await self._all(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )

    # --------------------------------------------------------- receipts
    async def create_receipt(self, user_id: int, amount: int) -> int:
        async with self.tx() as c:
            cur = await c.execute(
                "INSERT INTO receipts(user_id, amount, created_at) VALUES(?,?,?)", (user_id, amount, now())
            )
            return cur.lastrowid

    async def set_receipt_admin_msgs(self, receipt_id: int, msgs: list[list[int]]) -> None:
        await self._exec("UPDATE receipts SET admin_msgs=? WHERE id=?", (json.dumps(msgs), receipt_id))

    async def get_receipt(self, receipt_id: int) -> dict | None:
        row = await self._one("SELECT * FROM receipts WHERE id=?", (receipt_id,))
        if row:
            row["admin_msgs"] = json.loads(row["admin_msgs"] or "[]")
        return row

    async def decide_receipt(self, receipt_id: int, approve: bool, admin_id: int) -> tuple[dict, int | None] | None:
        """
        تایید/رد رسید. فقط رسید «pending» قابل تصمیم‌گیری است و شارژ کیف پول
        در همان تراکنش انجام می‌شود، پس دوبار کلیک شارژ دوباره نمی‌دهد.
        خروجی: (رسید، موجودی جدید در صورت تایید) یا None اگر قبلاً بررسی شده.
        """
        async with self.tx() as c:
            cur = await c.execute(
                "UPDATE receipts SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                ("approved" if approve else "rejected", admin_id, now(), receipt_id),
            )
            if cur.rowcount != 1:
                return None
            cur = await c.execute("SELECT * FROM receipts WHERE id=?", (receipt_id,))
            receipt = dict(await cur.fetchone())
            receipt["admin_msgs"] = json.loads(receipt["admin_msgs"] or "[]")
            new_balance = None
            if approve:
                new_balance = await self._adjust(
                    c, receipt["user_id"], receipt["amount"], "topup", f"شارژ حساب (رسید #{receipt_id})"
                )
            return receipt, new_balance

    # ------------------------------------------------------------ plans
    async def add_plan(self, name: str, days: int, price: int) -> int:
        async with self.tx() as c:
            cur = await c.execute(
                "INSERT INTO plans(name, duration_days, price, sort_order) "
                "VALUES(?,?,?,COALESCE((SELECT MAX(sort_order)+1 FROM plans),0))",
                (name, days, price),
            )
            return cur.lastrowid

    async def list_plans(self, active_only: bool = False) -> list[dict]:
        where = "WHERE active=1" if active_only else ""
        return await self._all(f"SELECT * FROM plans {where} ORDER BY sort_order, id")

    async def get_plan(self, plan_id: int) -> dict | None:
        return await self._one("SELECT * FROM plans WHERE id=?", (plan_id,))

    async def set_plan_price(self, plan_id: int, price: int) -> None:
        await self._exec("UPDATE plans SET price=? WHERE id=?", (price, plan_id))

    async def toggle_plan(self, plan_id: int) -> None:
        await self._exec("UPDATE plans SET active = 1 - active WHERE id=?", (plan_id,))

    async def delete_plan(self, plan_id: int) -> None:
        await self._exec("DELETE FROM plans WHERE id=?", (plan_id,))

    # --------------------------------------------------------- services
    async def purchase(self, user_id: int, plan_id: int, first_step: str) -> tuple[str, int | None]:
        """
        خرید سرویس از موجودی: کسر موجودی، ثبت تراکنش و ساخت سرویس در یک تراکنش اتمیک.
        خروجی: ("ok", service_id) | ("no_plan", None) | ("insufficient", None)
        """
        async with self.tx() as c:
            cur = await c.execute("SELECT * FROM plans WHERE id=? AND active=1", (plan_id,))
            plan = await cur.fetchone()
            if plan is None:
                return "no_plan", None
            new_balance = await self._adjust(c, user_id, -plan["price"], "purchase", f"خرید {plan['name']}")
            if new_balance is None:
                return "insufficient", None
            t = now()
            cur = await c.execute(
                "INSERT INTO services(user_id, plan_id, plan_name, price, duration_days, status, install_step,"
                " created_at, start_at, end_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (user_id, plan["id"], plan["name"], plan["price"], plan["duration_days"],
                 "awaiting_info", first_step, t, t, t + plan["duration_days"] * 86400),
            )
            return "ok", cur.lastrowid

    async def get_service(self, service_id: int) -> dict | None:
        return await self._one("SELECT * FROM services WHERE id=?", (service_id,))

    async def user_services(self, user_id: int) -> list[dict]:
        return await self._all("SELECT * FROM services WHERE user_id=? ORDER BY id DESC", (user_id,))

    async def recent_services(self, limit: int = 20) -> list[dict]:
        return await self._all("SELECT * FROM services ORDER BY id DESC LIMIT ?", (limit,))

    async def token_in_use(self, token: str, exclude_service_id: int | None = None) -> bool:
        row = await self._one(
            "SELECT 1 AS x FROM services WHERE bot_token=? AND id IS NOT ?", (token, exclude_service_id)
        )
        return row is not None

    async def update_install(self, service_id: int, step: str | None, data: dict) -> bool:
        """ذخیره‌ی پیشرفت مراحل نصب. اگر توکن تکراری باشد False برمی‌گردد."""
        try:
            await self._exec(
                "UPDATE services SET install_step=?, install_data=?, bot_token=?, bot_username=?, bot_name=? "
                "WHERE id=?",
                (step, json.dumps(data, ensure_ascii=False), data.get("bot_token"),
                 data.get("bot_username"), data.get("store_name"), service_id),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    async def set_service_status(self, service_id: int, status: str) -> None:
        await self._exec("UPDATE services SET status=? WHERE id=?", (status, service_id))

    async def finish_install_info(self, service_id: int) -> bool:
        """فقط وقتی سرویس هنوز در انتظار اطلاعات است به صف نصب می‌رود."""
        n = await self._exec(
            "UPDATE services SET status='pending_install', install_step=NULL "
            "WHERE id=? AND status='awaiting_info'",
            (service_id,),
        )
        return n == 1

    async def extend_service(self, service_id: int, days: int) -> None:
        await self._exec(
            "UPDATE services SET end_at = MAX(end_at, ?) + ?, reminded=0, "
            "status = CASE WHEN status='expired' THEN 'pending_install' ELSE status END WHERE id=?",
            (now(), days * 86400, service_id),
        )

    async def expire_due(self) -> list[dict]:
        async with self.tx() as c:
            cur = await c.execute(
                "SELECT * FROM services WHERE status!='expired' AND end_at<=?", (now(),)
            )
            rows = [dict(r) for r in await cur.fetchall()]
            await c.execute("UPDATE services SET status='expired' WHERE status!='expired' AND end_at<=?", (now(),))
            return rows

    async def due_soon(self, days: int) -> list[dict]:
        async with self.tx() as c:
            cur = await c.execute(
                "SELECT * FROM services WHERE status='active' AND reminded=0 AND end_at>? AND end_at<=?",
                (now(), now() + days * 86400),
            )
            rows = [dict(r) for r in await cur.fetchall()]
            for r in rows:
                await c.execute("UPDATE services SET reminded=1 WHERE id=?", (r["id"],))
            return rows

    # --------------------------------------------------------- settings
    async def get_setting(self, key: str, default: str = "") -> str:
        row = await self._one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self._exec(
            "INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ------------------------------------------------------------ stats
    async def stats(self) -> dict:
        row = await self._one(
            "SELECT (SELECT COUNT(*) FROM users) AS users,"
            " (SELECT COUNT(*) FROM services) AS services,"
            " (SELECT COUNT(*) FROM services WHERE status='active' AND end_at>?) AS active,"
            " (SELECT COUNT(*) FROM services WHERE status IN ('pending_install','installing')) AS queue,"
            " (SELECT COALESCE(SUM(price),0) FROM services) AS revenue,"
            " (SELECT COUNT(*) FROM receipts WHERE status='pending') AS pending_receipts",
            (now(),),
        )
        return row or {}

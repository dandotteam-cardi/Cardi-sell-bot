from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _ints(raw: str | None) -> tuple[int, ...]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return tuple(dict.fromkeys(out))


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: tuple[int, ...] = _ints(os.getenv("ADMIN_USER_IDS"))
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "seller.sqlite3")

TOPUP_PRESETS: tuple[int, ...] = (50_000, 100_000, 200_000, 500_000, 1_000_000)
MIN_TOPUP: int = int(os.getenv("MIN_TOPUP", "10000"))
MAX_TOPUP: int = int(os.getenv("MAX_TOPUP", "100000000"))
EXPIRY_REMINDER_DAYS: int = int(os.getenv("EXPIRY_REMINDER_DAYS", "3"))
# نصب خودکار ربات مشتری‌ها روی همین سرور (نیاز به sellbot-provision و sudoers؛ install.sh آن‌ها را می‌سازد)
PROVISION_ENABLED: bool = os.getenv("PROVISION_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def require() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN در فایل .env تنظیم نشده است.")
    if not ADMIN_IDS:
        raise RuntimeError("ADMIN_USER_IDS (آیدی عددی ادمین‌ها) در فایل .env تنظیم نشده است.")
from __future__ import annotations

import html
import time
from datetime import datetime

try:
    import jdatetime
except ImportError:  # تاریخ میلادی به‌عنوان جایگزین
    jdatetime = None

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

esc = html.escape

STATUS_LABELS = {
    "awaiting_info": "⏳ در انتظار تکمیل اطلاعات",
    "pending_install": "🛠 در صف نصب",
    "installing": "⚙️ در حال نصب",
    "active": "✅ فعال",
    "expired": "⛔️ منقضی",
    "stopped": "⏸ متوقف",
    "removed": "🗑 حذف‌شده",
}


def norm_digits(s: str) -> str:
    return s.translate(_DIGITS)


def money(n: int) -> str:
    return f"{n:,}"


def fmt_date(ts: int | None) -> str:
    if not ts:
        return "—"
    if jdatetime is not None:
        return jdatetime.datetime.fromtimestamp(ts).strftime("%Y/%m/%d")
    return datetime.fromtimestamp(ts).strftime("%Y/%m/%d")


def fmt_datetime(ts: int | None) -> str:
    if not ts:
        return "—"
    if jdatetime is not None:
        return jdatetime.datetime.fromtimestamp(ts).strftime("%Y/%m/%d %H:%M")
    return datetime.fromtimestamp(ts).strftime("%Y/%m/%d %H:%M")


def effective_status(service: dict) -> str:
    """وضعیت واقعی سرویس؛ اگر زمانش گذشته باشد منقضی حساب می‌شود."""
    if service["status"] != "expired" and service["end_at"] <= int(time.time()):
        return "expired"
    return service["status"]


def days_left(service: dict) -> int:
    left = service["end_at"] - int(time.time())
    return max(0, -(-left // 86400))


def status_label(service: dict) -> str:
    return STATUS_LABELS.get(effective_status(service), service["status"])
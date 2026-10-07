from __future__ import annotations

import json
import logging
import time

from telegram import Bot, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from telegram.constants import ParseMode
from telegram.error import TelegramError

import config
import install_flow as flow
import provisioner
from db import Database
from utils import esc

logger = logging.getLogger(__name__)
_running: set[int] = set()


async def _tell(bot: Bot, chat_id: int, text: str, markup=None) -> None:
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except TelegramError as exc:
        logger.warning("send to %s failed: %s", chat_id, exc)


async def _tell_admins(bot: Bot, text: str, markup=None) -> None:
    for admin_id in config.ADMIN_IDS:
        await _tell(bot, admin_id, text, markup)


async def run(bot: Bot, db: Database, sid: int) -> None:
    """نصب خودکار ربات مشتری روی همین سرور (پوشه: customers/<user_id>-<order_id>) و فعال‌کردن سرویس."""
    if not provisioner.enabled() or sid in _running:
        return
    _running.add(sid)
    try:
        s = await db.get_service(sid)
        if s is None or s["status"] not in ("pending_install", "installing") or s["end_at"] <= int(time.time()):
            return
        slug = provisioner.slug_for(s)
        await db.set_service_status(sid, "installing")
        try:
            env_text = flow.render_env(flow.build_env(json.loads(s["install_data"] or "{}")))
            ok, out = await provisioner.install(slug, env_text)
        except Exception as exc:  # noqa: BLE001
            ok, out = False, f"error: {exc}"

        now = await db.get_service(sid)
        if now is None:
            return
        if now["status"] == "expired":  # در حین نصب منقضی شد
            await provisioner.remove(slug)
            return

        if not ok:
            await db.set_service_status(sid, "pending_install")
            await _tell_admins(
                bot,
                f"❌ <b>نصب خودکار سفارش #{sid} ناموفق بود.</b>\n\n<pre>{esc(out[-900:])}</pre>",
                Markup([[Btn("🔁 تلاش مجدد", callback_data=f"adm:botact:auto:{sid}")],
                        [Btn("📄 باز کردن سفارش", callback_data=f"adm:bot:{sid}")]]),
            )
            return

        await db.set_service_status(sid, "active")
        await _tell(
            bot, s["user_id"],
            f"🎉 ربات شما نصب و فعال شد: @{esc(s['bot_username'] or '')}\n"
            "ربات را در تلگرام /start کنید و ادمین‌های تعریف‌شده می‌توانند آن را مدیریت کنند.",
        )
        await _tell_admins(bot, f"✅ سفارش #{sid} (@{esc(s['bot_username'] or '-')}) به‌صورت خودکار نصب و فعال شد "
                                f"— پوشه: <code>{slug}</code>")
    finally:
        _running.discard(sid)

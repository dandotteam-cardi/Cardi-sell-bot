from __future__ import annotations

import asyncio
import functools
import json
import logging
from types import SimpleNamespace

from telegram import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup as Markup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import admin
import autoinstall
import config
import install_flow as flow
import provisioner
from db import Database
from utils import (
    days_left, effective_status, esc, fmt_date, fmt_datetime, money, norm_digits, status_label,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # جلوگیری از چاپ توکن در لاگ
logger = logging.getLogger("seller")

BTN_BUY = "🛒 خرید سرویس"
BTN_WALLET = "💰 شارژ حساب"
BTN_ACCOUNT = "👤 حساب کاربری"
BTN_BOTS = "🤖 ربات‌های من"
BTN_SUPPORT = "💬 پشتیبانی"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_BUY), KeyboardButton(BTN_WALLET)],
         [KeyboardButton(BTN_ACCOUNT), KeyboardButton(BTN_BOTS)],
         [KeyboardButton(BTN_SUPPORT)]],
        resize_keyboard=True,
    )


def db_of(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.bot_data["db"]


def user_lock(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> asyncio.Lock:
    return context.bot_data.setdefault("locks", {}).setdefault(user_id, asyncio.Lock())


async def send(context, chat_id: int, text: str, markup=None):
    return await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def respond(update: Update, text: str, markup=None) -> None:
    """اگر از دکمه‌ی شیشه‌ای آمده پیام را ویرایش می‌کند، وگرنه پیام جدید می‌فرستد."""
    q = update.callback_query
    if q is not None and q.message is not None:
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def user_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user, chat = update.effective_user, update.effective_chat
        if user is None or chat is None or chat.type != "private":
            return
        row, _ = await db_of(context).upsert_user(user.id, user.username, user.full_name)
        if row["banned"]:
            if update.callback_query:
                await update.callback_query.answer("⛔️ دسترسی شما مسدود شده است.", show_alert=True)
            else:
                await update.effective_message.reply_text("⛔️ دسترسی شما مسدود شده است.")
            return
        if update.callback_query:
            await update.callback_query.answer()
        return await fn(update, context)
    return wrapper


async def notify_admins(context, text: str, markup=None) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await send(context, admin_id, text, markup)
        except TelegramError as exc:
            logger.warning("notify admin %s failed: %s", admin_id, exc)


# ======================================================================= start
@user_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    brand = await db_of(context).get_setting("brand", "ربات فروش ربات")
    await update.effective_message.reply_text(
        f"👋 به <b>{esc(brand)}</b> خوش آمدید.\n\nاز منوی زیر یکی از گزینه‌ها را انتخاب کنید.",
        parse_mode=ParseMode.HTML, reply_markup=main_menu(),
    )


# ====================================================================== account
@user_only
async def account_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = db_of(context)
    user = await db.get_user(update.effective_user.id)
    services = await db.user_services(user["user_id"])
    active = sum(1 for s in services if effective_status(s) in ("active", "pending_install", "installing", "awaiting_info"))
    username = f"@{esc(user['username'])}" if user["username"] else "—"
    text = (
        "👤 <b>حساب کاربری</b>\n\n"
        f"🆔 آیدی عددی: <code>{user['user_id']}</code>\n"
        f"📛 نام: {esc(user['full_name'] or '—')}\n"
        f"🔖 یوزرنیم: {username}\n"
        f"📅 تاریخ عضویت: {fmt_date(user['created_at'])}\n\n"
        f"💰 موجودی: <b>{money(user['balance'])} تومان</b>\n"
        f"🤖 ربات‌های جاری: <b>{active}</b> از {len(services)}"
    )
    markup = Markup([
        [Btn("💰 شارژ حساب", callback_data="wal:menu"), Btn("📜 تراکنش‌ها", callback_data="acc:tx")],
        [Btn("🤖 ربات‌های من", callback_data="acc:bots")],
    ])
    await respond(update, text, markup)


@user_only
async def transactions_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    txs = await db_of(context).list_transactions(update.effective_user.id, 15)
    if not txs:
        await respond(update, "📜 هنوز تراکنشی ثبت نشده است.", Markup([[Btn("🔙 بازگشت", callback_data="acc:home")]]))
        return
    lines = []
    for t in txs:
        sign = "➕" if t["amount"] > 0 else "➖"
        lines.append(f"{sign} <b>{money(abs(t['amount']))}</b> — {esc(t['description'] or t['kind'])}\n"
                     f"<code>{fmt_datetime(t['created_at'])}</code> · موجودی: {money(t['balance_after'])}")
    await respond(update, "📜 <b>۱۵ تراکنش آخر</b>\n\n" + "\n\n".join(lines),
                  Markup([[Btn("🔙 بازگشت", callback_data="acc:home")]]))


@user_only
async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    username = (await db_of(context).get_setting("support_username")).lstrip("@")
    if not username:
        await update.effective_message.reply_text("💬 برای پشتیبانی با ادمین در ارتباط باشید.")
        return
    await update.effective_message.reply_text(
        "💬 برای ارتباط با پشتیبانی دکمه‌ی زیر را بزنید.",
        reply_markup=Markup([[Btn("ارتباط با پشتیبانی", url=f"https://t.me/{username}")]]),
    )


# ====================================================================== my bots
def _service_title(s: dict) -> str:
    return s["bot_name"] or s["plan_name"]


@user_only
async def my_bots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    services = await db_of(context).user_services(update.effective_user.id)
    if not services:
        await respond(update, "🤖 هنوز ربات یا سرویسی نخریده‌اید.\nاز «🛒 خرید سرویس» شروع کنید.")
        return
    rows = [[Btn(f"{_service_title(s)} · {status_label(s)}", callback_data=f"bot:{s['id']}")] for s in services[:30]]
    await respond(update, "🤖 <b>ربات‌های من</b>\n\nبرای دیدن جزئیات یکی را انتخاب کنید:", Markup(rows))


@user_only
async def bot_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sid = int(update.callback_query.data.split(":")[1])
    s = await db_of(context).get_service(sid)
    if s is None or s["user_id"] != update.effective_user.id:
        return
    uname = f"@{esc(s['bot_username'])}" if s["bot_username"] else "—"
    eff = effective_status(s)
    remaining = f"\n⏱ روز باقی‌مانده: <b>{days_left(s)}</b>" if eff != "expired" else ""
    text = (
        f"🤖 <b>{esc(_service_title(s))}</b>\n\n"
        f"🔖 ربات: {uname}\n"
        f"📦 سرویس: {esc(s['plan_name'])}\n"
        f"💵 مبلغ: {money(s['price'])} تومان\n"
        f"📅 شروع: {fmt_date(s['start_at'])}\n"
        f"📅 پایان: {fmt_date(s['end_at'])}{remaining}\n"
        f"📍 وضعیت: {status_label(s)}"
    )
    rows = []
    if s["status"] == "awaiting_info" and eff != "expired":
        rows.append([Btn("▶️ ادامه‌ی تکمیل اطلاعات", callback_data=f"inst:resume:{sid}")])
    rows.append([Btn("🔙 بازگشت", callback_data="acc:bots")])
    await respond(update, text, Markup(rows))


# ======================================================================= wallet
def _topup_keyboard() -> Markup:
    amounts = list(config.TOPUP_PRESETS)
    rows = [[Btn(f"{money(a)} تومان", callback_data=f"wal:amt:{a}") for a in amounts[i:i + 2]]
            for i in range(0, len(amounts), 2)]
    rows.append([Btn("✏️ مبلغ دلخواه", callback_data="wal:custom")])
    return Markup(rows)


@user_only
async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("state", None)
    user = await db_of(context).get_user(update.effective_user.id)
    await respond(
        update,
        f"💰 <b>شارژ حساب</b>\n\nموجودی فعلی: <b>{money(user['balance'])} تومان</b>\n\n"
        "مبلغ موردنظر خود را انتخاب کنید:",
        _topup_keyboard(),
    )


async def _begin_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: int) -> None:
    db = db_of(context)
    card = await db.get_setting("card_number")
    if not card:
        username = (await db.get_setting("support_username")).lstrip("@")
        markup = Markup([[Btn("ارتباط با پشتیبانی", url=f"https://t.me/{username}")]]) if username else None
        await respond(update, "⚠️ شارژ خودکار فعلاً فعال نیست. لطفاً با پشتیبانی در ارتباط باشید.", markup)
        return
    holder = await db.get_setting("card_holder")
    context.user_data["state"] = {"name": "receipt", "amount": amount}
    holder_line = f"👤 به نام: {esc(holder)}\n" if holder else ""
    await respond(
        update,
        f"💳 <b>پرداخت {money(amount)} تومان</b>\n\n"
        f"لطفاً مبلغ را به کارت زیر واریز کنید:\n\n<code>{esc(card)}</code>\n{holder_line}\n"
        "بعد از واریز، <b>عکس رسید</b> را همین‌جا ارسال کنید. پس از تأیید ادمین، مبلغ به موجودی شما اضافه می‌شود.",
        Markup([[Btn("❌ انصراف", callback_data="wal:cancel")]]),
    )


@user_only
async def wallet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = update.callback_query.data.split(":")
    action = parts[1]
    if action == "menu":
        await wallet_menu.__wrapped__(update, context)
    elif action == "amt" and len(parts) == 3 and parts[2].isdigit():
        amount = int(parts[2])
        if amount in config.TOPUP_PRESETS:
            await _begin_receipt(update, context, amount)
    elif action == "custom":
        context.user_data["state"] = {"name": "topup_amount"}
        await respond(
            update,
            f"✏️ مبلغ موردنظر را به تومان و با عدد بفرستید.\n"
            f"<blockquote>حداقل {money(config.MIN_TOPUP)} و حداکثر {money(config.MAX_TOPUP)} تومان</blockquote>",
            Markup([[Btn("❌ انصراف", callback_data="wal:cancel")]]),
        )
    elif action == "cancel":
        context.user_data.pop("state", None)
        await respond(update, "❌ عملیات لغو شد.")


async def _handle_topup_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = norm_digits(update.effective_message.text or "").replace(",", "").strip()
    if not raw.isdigit() or not config.MIN_TOPUP <= int(raw) <= config.MAX_TOPUP:
        await update.effective_message.reply_text(
            f"❗️ یک عدد معتبر بین {money(config.MIN_TOPUP)} و {money(config.MAX_TOPUP)} تومان بفرستید."
        )
        return
    await _begin_receipt(update, context, int(raw))


@user_only
async def receipt_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    if not state or state.get("name") != "receipt":
        return
    msg, user, db = update.effective_message, update.effective_user, db_of(context)
    amount = state["amount"]
    is_photo = bool(msg.photo)
    if not is_photo and not (msg.document and (msg.document.mime_type or "").startswith("image/")):
        await msg.reply_text("❗️ لطفاً رسید را به‌صورت عکس ارسال کنید.")
        return
    context.user_data.pop("state", None)
    receipt_id = await db.create_receipt(user.id, amount)
    caption = (
        f"🧾 <b>رسید شارژ حساب</b> #{receipt_id}\n\n"
        f"👤 {esc(user.full_name)} {('(@' + esc(user.username) + ')') if user.username else ''}\n"
        f"🆔 <code>{user.id}</code>\n"
        f"💰 مبلغ اعلام‌شده: <b>{money(amount)} تومان</b>"
    )
    markup = Markup([[Btn("✅ تایید", callback_data=f"rcpt:ok:{receipt_id}"),
                      Btn("❌ رد", callback_data=f"rcpt:no:{receipt_id}")]])
    sent: list[list[int]] = []
    for admin_id in config.ADMIN_IDS:
        try:
            if is_photo:
                m = await context.bot.send_photo(admin_id, msg.photo[-1].file_id, caption=caption,
                                                 parse_mode=ParseMode.HTML, reply_markup=markup)
            else:
                m = await context.bot.send_document(admin_id, msg.document.file_id, caption=caption,
                                                    parse_mode=ParseMode.HTML, reply_markup=markup)
            sent.append([admin_id, m.message_id])
        except TelegramError as exc:
            logger.warning("receipt to admin %s failed: %s", admin_id, exc)
    if not sent:
        await db.decide_receipt(receipt_id, False, 0)
        await msg.reply_text("⚠️ ارسال رسید به ادمین ناموفق بود. لطفاً با پشتیبانی در ارتباط باشید.")
        return
    await db.set_receipt_admin_msgs(receipt_id, sent)
    await msg.reply_text(
        f"✅ رسید شما ({money(amount)} تومان) دریافت شد و پس از بررسی ادمین به موجودی اضافه می‌شود.",
        reply_markup=main_menu(),
    )


async def receipt_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.from_user is None:
        return
    if not config.is_admin(q.from_user.id):
        await q.answer("شما ادمین نیستید.", show_alert=True)
        return
    _, action, rid = q.data.split(":")
    approve = action == "ok"
    outcome = await db_of(context).decide_receipt(int(rid), approve, q.from_user.id)
    if outcome is None:
        await q.answer("این رسید قبلاً بررسی شده است.", show_alert=True)
        return
    receipt, new_balance = outcome
    if approve:
        user_text = (f"✅ رسید شما تایید شد.\n💰 مبلغ <b>{money(receipt['amount'])} تومان</b> به حساب شما اضافه شد.\n"
                     f"موجودی فعلی: <b>{money(new_balance or 0)} تومان</b>")
        status = f"\n\n✅ <b>تایید شد</b> توسط {esc(q.from_user.full_name)}"
    else:
        user_text = f"❌ رسید شما ({money(receipt['amount'])} تومان) رد شد. در صورت ابهام با پشتیبانی تماس بگیرید."
        status = f"\n\n❌ <b>رد شد</b> توسط {esc(q.from_user.full_name)}"
    try:
        await send(context, receipt["user_id"], user_text)
    except TelegramError:
        status += "\n⚠️ ارسال پیام به کاربر ناموفق بود."
    base = q.message.caption_html if q.message and q.message.caption else f"🧾 رسید #{rid}"
    for chat_id, message_id in receipt["admin_msgs"] or [[q.message.chat_id, q.message.message_id]]:
        try:
            await context.bot.edit_message_caption(chat_id, message_id, caption=base + status,
                                                   parse_mode=ParseMode.HTML, reply_markup=None)
        except TelegramError:
            pass
    await q.answer("تایید شد ✅" if approve else "رد شد ❌")


# ======================================================================= buying
@user_only
async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db = db_of(context)
    plans = await db.list_plans(active_only=True)
    if not plans:
        await respond(update, "⚠️ در حال حاضر سرویسی برای فروش موجود نیست.")
        return
    title = await db.get_setting("service_title", "🤖 ربات فروش کانفیگ")
    rows = [[Btn(f"▫️ {p['name']} — {money(p['price'])} تومان", callback_data=f"buy:p:{p['id']}")] for p in plans]
    await respond(update, f"<b>{esc(title)}</b>\n\nیکی از سرویس‌ها را انتخاب کنید:", Markup(rows))


@user_only
async def buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = update.callback_query.data.split(":")
    action, pid = parts[1], int(parts[2]) if len(parts) > 2 else 0
    if action == "list":
        await buy_menu.__wrapped__(update, context)
        return
    db, user_id = db_of(context), update.effective_user.id
    plan = await db.get_plan(pid)
    if plan is None or not plan["active"]:
        await respond(update, "⚠️ این سرویس دیگر در دسترس نیست.", Markup([[Btn("🔙 بازگشت", callback_data="buy:list:0")]]))
        return
    if action == "p":
        user = await db.get_user(user_id)
        text = (f"🧾 <b>تایید خرید</b>\n\n📦 سرویس: <b>{esc(plan['name'])}</b>\n"
                f"⏳ مدت: {plan['duration_days']} روز\n💵 قیمت: <b>{money(plan['price'])} تومان</b>\n"
                f"💰 موجودی شما: {money(user['balance'])} تومان")
        rows = []
        if user["balance"] >= plan["price"]:
            rows.append([Btn("✅ پرداخت از موجودی", callback_data=f"buy:ok:{pid}")])
        else:
            text += f"\n\n⚠️ موجودی کافی نیست؛ <b>{money(plan['price'] - user['balance'])} تومان</b> کم دارید."
            rows.append([Btn("💰 شارژ حساب", callback_data="wal:menu")])
        rows.append([Btn("🔙 بازگشت", callback_data="buy:list:0")])
        await respond(update, text, Markup(rows))
    elif action == "ok":
        async with user_lock(context, user_id):  # جلوگیری از دوبار کلیک هم‌زمان
            result, sid = await db.purchase(user_id, pid, flow.FIRST_STEP)
        if result == "insufficient":
            await respond(update, "⚠️ موجودی کافی نیست.", Markup([[Btn("💰 شارژ حساب", callback_data="wal:menu")]]))
            return
        if result != "ok":
            await respond(update, "⚠️ این سرویس دیگر در دسترس نیست.")
            return
        await respond(update, f"✅ خرید <b>{esc(plan['name'])}</b> با موفقیت انجام شد.\n"
                              f"{money(plan['price'])} تومان از موجودی شما کسر شد.")
        await begin_install(context, update.effective_chat.id, user_id, sid)


# =============================================================== install-info
def _step_keyboard(step: flow.Step, data: dict, sid: int) -> Markup:
    rows: list[list[Btn]] = []
    if step.kind == "choice":
        rows.append([Btn(label, callback_data=f"inst:c:{sid}:{i}") for i, (label, _) in enumerate(step.choices)])
    if step.me_button:
        rows.append([Btn("👤 فقط خودم", callback_data=f"inst:me:{sid}")])
    if step.optional:
        rows.append([Btn("⏭ رد کردن", callback_data=f"inst:skip:{sid}")])
    nav = []
    if flow.prev_step(data, step.key) is not None:
        nav.append(Btn("⬅️ مرحله‌ی قبل", callback_data=f"inst:back:{sid}"))
    nav.append(Btn("⏸ ادامه بعداً", callback_data=f"inst:pause:{sid}"))
    rows.append(nav)
    return Markup(rows)


async def send_step(context, chat_id: int, service: dict, step: flow.Step) -> None:
    data = json.loads(service["install_data"])
    n, total = flow.position(data, step.key)
    await send(context, chat_id, f"<b>مرحله {n} از {total}</b>\n\n{step.prompt}",
               _step_keyboard(step, data, service["id"]))


def _mask(value: str, keep: int = 6) -> str:
    return value[:keep] + "…" if len(value) > keep else "••••"


def _summary(data: dict) -> str:
    def line(label: str, key: str, secret: bool = False) -> str:
        v = data.get(key)
        return f"{label}: <code>{esc(_mask(v) if secret else v)}</code>\n" if v else ""
    out = "📋 <b>خلاصه‌ی اطلاعات ربات</b>\n\n"
    out += f"🤖 ربات: @{esc(data.get('bot_username', ''))}\n"
    out += line("🔑 توکن", "bot_token", True) + line("🏷 نام", "store_name") + line("👮 ادمین‌ها", "admins")
    out += line("📢 کانال کانفیگ", "source_channel") + line("🗄 کانال محتوا", "data_channel")
    out += line("🧾 کانال لاگ", "log_channel") + line("💬 پشتیبانی", "support_username")
    if data.get("panel_connect") == "yes":
        out += "\n🔌 <b>پنل</b>\n" + line("نوع", "panel_type") + line("آدرس", "panel_url")
        out += line("حالت API", "panel_api_mode") + line("نام‌کاربری", "panel_username")
        out += line("رمز", "panel_password", True) + line("توکن API", "panel_api_token", True)
        out += line("اینباند", "panel_inbounds") + line("سابسکریپشن", "panel_sub_base")
    else:
        out += "\n🔌 پنل: بعداً از داخل ربات متصل می‌شود\n"
    if data.get("advanced") == "yes":
        out += "\n🛠 <b>پیشرفته</b>\n" + line("عضویت اجباری", "force_sub") + line("چرخ شانس", "wheel_channel")
        out += line("رفرال", "referral_channel") + line("لینک آموزش", "tutorial_link")
        out += line("شماره کارت", "support_address")
    return out + "\nاگر اطلاعات درست است تایید کنید."


async def send_summary(context, chat_id: int, service: dict) -> None:
    data = json.loads(service["install_data"])
    sid = service["id"]
    await send(context, chat_id, _summary(data), Markup([
        [Btn("✅ تایید و ثبت برای نصب", callback_data=f"inst:confirm:{sid}")],
        [Btn("⬅️ مرحله‌ی قبل", callback_data=f"inst:back:{sid}"), Btn("🔁 شروع از اول", callback_data=f"inst:restart:{sid}")],
    ]))


async def begin_install(context, chat_id: int, user_id: int, sid: int) -> None:
    service = await db_of(context).get_service(sid)
    if service is None or service["user_id"] != user_id or service["status"] != "awaiting_info":
        return
    context.user_data["state"] = {"name": "install", "sid": sid}
    if service["install_step"] == "confirm":
        await send_summary(context, chat_id, service)
        return
    step = flow.get_step(service["install_step"]) or flow.get_step(flow.FIRST_STEP)
    await send_step(context, chat_id, service, step)


async def _advance(context, chat_id: int, service: dict, data: dict, after_key: str) -> None:
    db = db_of(context)
    nxt = flow.next_step(data, after_key)
    if not await db.update_install(service["id"], nxt.key if nxt else "confirm", data):
        await send(context, chat_id, "❗️ این توکن قبلاً برای سرویس دیگری ثبت شده است. توکن دیگری بفرستید.")
        return
    service = await db.get_service(service["id"])
    if nxt:
        await send_step(context, chat_id, service, nxt)
    else:
        await send_summary(context, chat_id, service)


async def _handle_install_text(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    db, msg = db_of(context), update.effective_message
    service = await db.get_service(state["sid"])
    if service is None or service["user_id"] != update.effective_user.id or service["status"] != "awaiting_info":
        context.user_data.pop("state", None)
        return
    step = flow.get_step(service["install_step"])
    if step is None:
        await msg.reply_text("برای ادامه از دکمه‌های پیام قبلی استفاده کنید.")
        return
    if step.kind == "choice":
        await msg.reply_text("لطفاً یکی از دکمه‌های همین مرحله را انتخاب کنید.")
        return
    raw = msg.text or ""
    if step.secret:  # توکن/رمز را از چت پاک می‌کنیم
        try:
            await msg.delete()
        except TelegramError:
            pass
    data = json.loads(service["install_data"])
    ctx = flow.Ctx(data=data, main_token=config.BOT_TOKEN,
                   token_in_use=lambda t: db.token_in_use(t, service["id"]))
    result = await step.validate(raw, ctx) if step.validate else flow.Result(value=raw.strip())
    chat_id = update.effective_chat.id
    if result.error:
        markup = None
        if result.soft:
            context.user_data["pending_value"] = {"sid": service["id"], "key": step.key, "value": result.value}
            markup = Markup([[Btn("⏭ ادامه بدون بررسی", callback_data=f"inst:force:{service['id']}")]])
        await send(context, chat_id, f"❗️ {esc(result.error)}", markup)
        return
    data[step.key] = result.value
    await _advance(context, chat_id, service, data, step.key)


@user_only
async def install_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = update.callback_query.data.split(":")
    action, sid = parts[1], int(parts[2])
    arg = parts[3] if len(parts) > 3 else None
    db, user_id, chat_id = db_of(context), update.effective_user.id, update.effective_chat.id
    service = await db.get_service(sid)
    if service is None or service["user_id"] != user_id:
        return
    if action == "resume":
        await begin_install(context, chat_id, user_id, sid)
        return
    if service["status"] != "awaiting_info":
        await respond(update, "ℹ️ اطلاعات این ربات قبلاً ثبت شده است.")
        return
    data = json.loads(service["install_data"])
    cur_key = service["install_step"]
    step = flow.get_step(cur_key)
    context.user_data["state"] = {"name": "install", "sid": sid}

    if action == "pause":
        context.user_data.pop("state", None)
        await respond(update, "⏸ ذخیره شد. هر زمان خواستید از «🤖 ربات‌های من» ادامه دهید.")
    elif action == "back":
        prev = flow.prev_step(data, cur_key)
        if prev is not None:
            await db.update_install(sid, prev.key, data)
            await send_step(context, chat_id, await db.get_service(sid), prev)
    elif action == "restart":
        await db.update_install(sid, flow.FIRST_STEP, data)
        await send_step(context, chat_id, await db.get_service(sid), flow.get_step(flow.FIRST_STEP))
    elif action == "c" and step and step.kind == "choice" and arg and arg.isdigit() and int(arg) < len(step.choices):
        data[step.key] = step.choices[int(arg)][1]
        await _advance(context, chat_id, service, data, step.key)
    elif action == "me" and step and step.me_button:
        data[step.key] = str(user_id)
        await _advance(context, chat_id, service, data, step.key)
    elif action == "skip" and step and step.optional:
        data.pop(step.key, None)
        await _advance(context, chat_id, service, data, step.key)
    elif action == "force":
        pending = context.user_data.pop("pending_value", None)
        if pending and pending["sid"] == sid and pending["key"] == cur_key and step:
            data[step.key] = pending["value"]
            await _advance(context, chat_id, service, data, step.key)
    elif action == "confirm" and cur_key == "confirm":
        missing = flow.missing_required(data)
        if missing:
            await db.update_install(sid, missing[0], data)
            await send_step(context, chat_id, await db.get_service(sid), flow.get_step(missing[0]))
            return
        if not await db.finish_install_info(sid):
            return
        context.user_data.pop("state", None)
        auto = provisioner.enabled()
        await respond(update, "✅ <b>اطلاعات ربات شما ثبت شد.</b>\n\n"
                              + ("ربات شما در حال نصب خودکار است و پس از راه‌اندازی به شما اطلاع داده می‌شود. "
                                 if auto else
                                 "ربات شما در صف نصب قرار گرفت و پس از راه‌اندازی به شما اطلاع داده می‌شود. ")
                              + "وضعیت را از «🤖 ربات‌های من» ببینید.")
        service = await db.get_service(sid)
        await notify_admins(
            context,
            f"🆕 <b>سفارش جدید برای نصب</b> #{sid}\n\n👤 <code>{user_id}</code>\n"
            f"🤖 @{esc(service['bot_username'] or '')} — {esc(service['bot_name'] or '')}\n"
            f"📦 {esc(service['plan_name'])} تا {fmt_date(service['end_at'])}",
            Markup([[Btn("📄 باز کردن سفارش", callback_data=f"adm:bot:{sid}")]]),
        )
        if auto:
            context.application.create_task(autoinstall.run(context.bot, db, sid))


# ========================================================================= text
MENU_ACTIONS = {
    BTN_BUY: buy_menu, BTN_WALLET: wallet_menu, BTN_ACCOUNT: account_menu,
    BTN_BOTS: my_bots, BTN_SUPPORT: support_menu,
}


@user_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.effective_message.text or "").strip()
    action = MENU_ACTIONS.get(text)
    if action is not None:
        context.user_data.pop("state", None)
        await action.__wrapped__(update, context)
        return
    state = context.user_data.get("state")
    if not state:
        return
    name = state["name"]
    if name == "topup_amount":
        await _handle_topup_amount(update, context)
    elif name == "receipt":
        await update.effective_message.reply_text("❗️ لطفاً رسید را به‌صورت عکس ارسال کنید.")
    elif name == "install":
        await _handle_install_text(update, context, state)


async def on_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پیام‌های متنی ادمین: ابتدا وضعیت‌های پنل ادمین، بعد منوی عادی."""
    user = update.effective_user
    if user is None or update.effective_chat.type != "private":
        return
    if config.is_admin(user.id):
        if await admin.handle_state(update, context):
            return
    await on_text(update, context)


# ===================================================================== account cb
@user_only
async def account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    action = update.callback_query.data.split(":")[1]
    if action == "home":
        await account_menu.__wrapped__(update, context)
    elif action == "tx":
        await transactions_view.__wrapped__(update, context)
    elif action == "bots":
        await my_bots.__wrapped__(update, context)


# ======================================================================== jobs
async def expiry_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    db = db_of(context)
    for s in await db.expire_due():
        try:
            await send(context, s["user_id"], f"⛔️ سرویس <b>{esc(_service_title(s))}</b> منقضی شد.")
        except TelegramError:
            pass
        note = f"⛔️ سرویس #{s['id']} (@{esc(s['bot_username'] or '-')}) منقضی شد."
        if provisioner.enabled():
            ok, out = await provisioner.remove(provisioner.slug_for(s))
            if ok:
                note += "\n🗑 ربات مشتری متوقف و از سرور حذف شد."
            elif "not-installed" not in out:
                note += "\n⚠️ حذف ربات روی سرور ناموفق بود؛ دستی بررسی کنید."
        await notify_admins(context, note)
    for s in await db.due_soon(config.EXPIRY_REMINDER_DAYS):
        try:
            await send(context, s["user_id"],
                       f"⏰ سرویس <b>{esc(_service_title(s))}</b> تا {fmt_date(s['end_at'])} اعتبار دارد.")
        except TelegramError:
            pass


# ======================================================================== main
async def post_init(app: Application) -> None:
    db = Database(config.DATABASE_PATH)
    await db.connect()
    app.bot_data["db"] = db
    if app.job_queue is not None:
        app.job_queue.run_repeating(expiry_job, interval=3600, first=30)
    else:  # python-telegram-bot[job-queue] نصب نیست؛ حلقه‌ی ساده‌ی جایگزین
        app.bot_data["expiry_task"] = asyncio.create_task(_expiry_loop(app))


async def _expiry_loop(app: Application) -> None:
    ctx = SimpleNamespace(bot=app.bot, bot_data=app.bot_data)
    await asyncio.sleep(30)
    while True:
        try:
            await expiry_job(ctx)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("expiry job failed")
        await asyncio.sleep(3600)


async def post_shutdown(app: Application) -> None:
    task = app.bot_data.get("expiry_task")
    if task is not None:
        task.cancel()
    db = app.bot_data.get("db")
    if db is not None:
        await db.close()


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled error", exc_info=context.error)


def main() -> None:
    config.require()
    builder = Application.builder().token(config.BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown)
    app = builder.build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))
    admin.register(app)

    app.add_handler(CallbackQueryHandler(wallet_callback, pattern=r"^wal:"))
    app.add_handler(CallbackQueryHandler(receipt_decision, pattern=r"^rcpt:(ok|no):\d+$"))
    app.add_handler(CallbackQueryHandler(buy_callback, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(install_callback, pattern=r"^inst:"))
    app.add_handler(CallbackQueryHandler(bot_detail, pattern=r"^bot:\d+$"))
    app.add_handler(CallbackQueryHandler(account_callback, pattern=r"^acc:"))

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.PHOTO | filters.Document.IMAGE), receipt_photo))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_admin_text))
    app.add_error_handler(on_error)

    # پایتون ۳.۱۴ دیگر event loop را خودکار نمی‌سازد؛ خودمان می‌سازیم.
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except NetworkError as exc:
        raise SystemExit(
            f"\n❌ اتصال به سرورهای تلگرام برقرار نشد ({exc}).\n"
            "   • VPN را روشن کنید (ترجیحاً حالت TUN/سیستمی) و دوباره اجرا کنید.\n"
        )


if __name__ == "__main__":
    main()
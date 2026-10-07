from __future__ import annotations

import functools
import io
import json

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

import autoinstall
import config
import install_flow as flow
import provisioner
from utils import days_left, esc, fmt_date, money, norm_digits, status_label

SETTING_LABELS = {
    "brand": "نام فروشگاه (پیام خوش‌آمد)",
    "service_title": "عنوان لیست سرویس‌ها",
    "card_number": "شماره کارت",
    "card_holder": "نام صاحب کارت",
    "support_username": "یوزرنیم پشتیبانی (بدون @)",
}

HOME = Markup([
    [Btn("📦 سرویس‌ها و قیمت‌ها", callback_data="adm:plans"), Btn("💳 تنظیمات", callback_data="adm:settings")],
    [Btn("👥 کاربران", callback_data="adm:users"), Btn("🤖 ربات‌های مشتریان", callback_data="adm:bots")],
    [Btn("📊 آمار", callback_data="adm:stats")],
])
BACK = Markup([[Btn("🔙 پنل مدیریت", callback_data="adm:home")]])


def db_of(context):
    return context.bot_data["db"]


async def _show(update: Update, text: str, markup=None) -> None:
    q = update.callback_query
    if q is not None and q.message is not None:
        try:
            await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        except BadRequest as exc:
            if "not modified" in str(exc).lower():
                return
    await update.effective_chat.send_message(text, parse_mode=ParseMode.HTML, reply_markup=markup)


def admin_only(fn):
    @functools.wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or not config.is_admin(user.id):
            if update.callback_query:
                await update.callback_query.answer("شما ادمین نیستید.", show_alert=True)
            return
        if update.callback_query:
            await update.callback_query.answer()
        return await fn(update, context)
    return wrapper


@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("state", None)
    await update.effective_message.reply_text("🛠 <b>پنل مدیریت</b>", parse_mode=ParseMode.HTML, reply_markup=HOME)


# ------------------------------------------------------------------ views
async def _plans_view(update, context) -> None:
    plans = await db_of(context).list_plans()
    rows = [[Btn(f"{'✅' if p['active'] else '🚫'} {p['name']} — {money(p['price'])}", callback_data=f"adm:plan:{p['id']}")]
            for p in plans]
    rows.append([Btn("➕ افزودن سرویس", callback_data="adm:plan_add")])
    rows.append([Btn("🔙 پنل مدیریت", callback_data="adm:home")])
    await _show(update, "📦 <b>سرویس‌ها</b>\n\nبرای ویرایش روی هر سرویس بزنید.", Markup(rows))


async def _plan_view(update, context, pid: int) -> None:
    p = await db_of(context).get_plan(pid)
    if p is None:
        await _plans_view(update, context)
        return
    text = (f"📦 <b>{esc(p['name'])}</b>\n\n⏳ مدت: {p['duration_days']} روز\n"
            f"💵 قیمت: {money(p['price'])} تومان\n📍 وضعیت: {'فعال' if p['active'] else 'غیرفعال'}")
    await _show(update, text, Markup([
        [Btn("✏️ تغییر قیمت", callback_data=f"adm:plan_price:{pid}"),
         Btn("🔁 فعال/غیرفعال", callback_data=f"adm:plan_toggle:{pid}")],
        [Btn("🗑 حذف", callback_data=f"adm:plan_del:{pid}")],
        [Btn("🔙 سرویس‌ها", callback_data="adm:plans")],
    ]))


async def _settings_view(update, context) -> None:
    db = db_of(context)
    lines, rows = [], []
    for key, label in SETTING_LABELS.items():
        value = await db.get_setting(key)
        lines.append(f"• {label}: <code>{esc(value) if value else '—'}</code>")
        rows.append([Btn(f"✏️ {label}", callback_data=f"adm:set:{key}")])
    rows.append([Btn("🔙 پنل مدیریت", callback_data="adm:home")])
    await _show(update, "💳 <b>تنظیمات</b>\n\n" + "\n".join(lines), Markup(rows))


async def _user_view(update, context, user: dict) -> None:
    services = await db_of(context).user_services(user["user_id"])
    uname = f"@{esc(user['username'])}" if user["username"] else "—"
    text = (f"👤 <b>{esc(user['full_name'] or '—')}</b> {uname}\n🆔 <code>{user['user_id']}</code>\n"
            f"💰 موجودی: <b>{money(user['balance'])} تومان</b>\n"
            f"🤖 سرویس‌ها: {len(services)}\n🚫 مسدود: {'بله' if user['banned'] else 'خیر'}")
    uid = user["user_id"]
    await _show(update, text, Markup([
        [Btn("➕ افزایش موجودی", callback_data=f"adm:ub:+:{uid}"), Btn("➖ کاهش موجودی", callback_data=f"adm:ub:-:{uid}")],
        [Btn("✅ رفع مسدودی" if user["banned"] else "🚫 مسدود کردن", callback_data=f"adm:ban:{uid}")],
        [Btn("🔙 پنل مدیریت", callback_data="adm:home")],
    ]))


async def _bots_view(update, context) -> None:
    services = await db_of(context).recent_services(25)
    if not services:
        await _show(update, "🤖 هنوز سفارشی ثبت نشده است.", BACK)
        return
    rows = [[Btn(f"#{s['id']} {s['bot_name'] or s['plan_name']} · {status_label(s)}", callback_data=f"adm:bot:{s['id']}")]
            for s in services]
    rows.append([Btn("🔙 پنل مدیریت", callback_data="adm:home")])
    await _show(update, "🤖 <b>ربات‌های مشتریان</b> (۲۵ سفارش آخر)", Markup(rows))


async def _bot_view(update, context, sid: int) -> None:
    s = await db_of(context).get_service(sid)
    if s is None:
        await _bots_view(update, context)
        return
    text = (f"🤖 <b>سفارش #{sid}</b>\n\n👤 مشتری: <code>{s['user_id']}</code>\n"
            f"🔖 ربات: @{esc(s['bot_username'] or '—')}\n🏷 نام: {esc(s['bot_name'] or '—')}\n"
            f"📦 {esc(s['plan_name'])} · {money(s['price'])} تومان\n"
            f"📅 {fmt_date(s['start_at'])} تا {fmt_date(s['end_at'])} ({days_left(s)} روز مانده)\n"
            f"📍 {status_label(s)}")
    rows = []
    if s["status"] in ("pending_install", "installing", "active", "expired"):
        rows.append([Btn("📄 فایل .env", callback_data=f"adm:env:{sid}")])
    if provisioner.enabled() and s["status"] in ("pending_install", "installing"):
        rows.append([Btn("🚀 نصب خودکار", callback_data=f"adm:botact:auto:{sid}")])
    if s["status"] in ("pending_install", "expired"):
        rows.append([Btn("⚙️ در حال نصب", callback_data=f"adm:botact:installing:{sid}")])
    if s["status"] in ("pending_install", "installing", "expired"):
        rows.append([Btn("✅ نصب شد (فعال)", callback_data=f"adm:botact:active:{sid}")])
    rows.append([Btn("➕ تمدید", callback_data=f"adm:extend:{sid}")])
    rows.append([Btn("🔙 لیست", callback_data="adm:bots")])
    await _show(update, text, Markup(rows))


async def _stats_view(update, context) -> None:
    st = await db_of(context).stats()
    await _show(update, (
        "📊 <b>آمار</b>\n\n"
        f"👥 کاربران: {st['users']}\n🤖 کل سرویس‌ها: {st['services']}\n✅ فعال: {st['active']}\n"
        f"🛠 در صف نصب: {st['queue']}\n💵 مجموع فروش: {money(st['revenue'])} تومان\n"
        f"🧾 رسیدهای در انتظار: {st['pending_receipts']}"), BACK)


# -------------------------------------------------------------- callbacks
@admin_only
async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    parts = update.callback_query.data.split(":")
    action, args = parts[1], parts[2:]
    db = db_of(context)
    ud = context.user_data

    if action == "home":
        ud.pop("state", None)
        await _show(update, "🛠 <b>پنل مدیریت</b>", HOME)
    elif action == "plans":
        await _plans_view(update, context)
    elif action == "plan":
        await _plan_view(update, context, int(args[0]))
    elif action == "plan_add":
        ud["state"] = {"name": "adm_plan_add", "stage": "name"}
        await _show(update, "📝 نام سرویس را بفرستید (مثلاً: <code>1 ماهه</code>).", BACK)
    elif action == "plan_price":
        ud["state"] = {"name": "adm_plan_price", "pid": int(args[0])}
        await _show(update, "💵 قیمت جدید را به تومان بفرستید.", BACK)
    elif action == "plan_toggle":
        await db.toggle_plan(int(args[0]))
        await _plan_view(update, context, int(args[0]))
    elif action == "plan_del":
        pid = int(args[0])
        await _show(update, "⚠️ این سرویس حذف شود؟ (سرویس‌های خریداری‌شده‌ی قبلی تغییری نمی‌کنند)", Markup([
            [Btn("🗑 بله، حذف شود", callback_data=f"adm:plan_delok:{pid}"), Btn("انصراف", callback_data=f"adm:plan:{pid}")]]))
    elif action == "plan_delok":
        await db.delete_plan(int(args[0]))
        await _plans_view(update, context)
    elif action == "settings":
        await _settings_view(update, context)
    elif action == "set" and args[0] in SETTING_LABELS:
        ud["state"] = {"name": "adm_set", "key": args[0]}
        await _show(update, f"✏️ مقدار جدید برای «{SETTING_LABELS[args[0]]}» را بفرستید.\n"
                            "برای خالی کردن مقدار، یک «-» بفرستید.", BACK)
    elif action == "users":
        ud["state"] = {"name": "adm_user_find"}
        await _show(update, "🔎 آیدی عددی یا @یوزرنیم کاربر را بفرستید.", BACK)
    elif action == "ub":
        ud["state"] = {"name": "adm_balance", "sign": args[0], "uid": int(args[1])}
        word = "افزایش" if args[0] == "+" else "کاهش"
        await _show(update, f"💰 مبلغ {word} موجودی را به تومان بفرستید.", BACK)
    elif action == "ban":
        uid = int(args[0])
        user = await db.get_user(uid)
        if user and not config.is_admin(uid):
            await db.set_banned(uid, not user["banned"])
            await _user_view(update, context, await db.get_user(uid))
    elif action == "bots":
        await _bots_view(update, context)
    elif action == "bot":
        await _bot_view(update, context, int(args[0]))
    elif action == "botact":
        status, sid = args[0], int(args[1])
        if status == "auto":
            if provisioner.enabled():
                context.application.create_task(autoinstall.run(context.bot, db, sid))
                await _bot_view(update, context, sid)
            return
        if status in ("installing", "active"):
            service = await db.get_service(sid)
            if service is None:
                return
            await db.set_service_status(sid, status)
            if status == "active":
                try:
                    await context.bot.send_message(
                        service["user_id"],
                        f"🎉 ربات شما نصب و فعال شد: @{esc(service['bot_username'] or '')}\n"
                        "ربات را در تلگرام /start کنید و ادمین‌های تعریف‌شده می‌توانند آن را مدیریت کنند.",
                        parse_mode=ParseMode.HTML)
                except TelegramError:
                    pass
            await _bot_view(update, context, sid)
    elif action == "env":
        service = await db.get_service(int(args[0]))
        if service is None:
            return
        env_text = flow.render_env(flow.build_env(json.loads(service["install_data"])))
        buf = io.BytesIO(env_text.encode())
        buf.name = f"service-{service['id']}.env"
        await context.bot.send_document(update.effective_chat.id, buf,
                                        caption=f"⚠️ شامل توکن و رمزها است. سفارش #{service['id']}")
    elif action == "extend":
        ud["state"] = {"name": "adm_extend", "sid": int(args[0])}
        await _show(update, "➕ چند روز تمدید شود؟ (عدد بفرستید)", BACK)
    elif action == "stats":
        await _stats_view(update, context)


# ------------------------------------------------------------ text states
def _int(raw: str) -> int | None:
    raw = norm_digits(raw).replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


async def handle_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پیام متنی ادمین را اگر مربوط به یک وضعیت پنل مدیریت باشد مصرف می‌کند."""
    state = context.user_data.get("state")
    if not state or not str(state.get("name", "")).startswith("adm_"):
        return False
    db, msg, ud = db_of(context), update.effective_message, context.user_data
    text = (msg.text or "").strip()
    name = state["name"]

    if name == "adm_plan_add":
        if state["stage"] == "name":
            if not 1 <= len(text) <= 60:
                await msg.reply_text("❗️ نام باید بین ۱ تا ۶۰ کاراکتر باشد.")
                return True
            state.update(stage="days", plan_name=text)
            await msg.reply_text("⏳ مدت سرویس را به <b>روز</b> بفرستید (مثلاً 30).", parse_mode=ParseMode.HTML)
        elif state["stage"] == "days":
            days = _int(text)
            if not days or days > 3650:
                await msg.reply_text("❗️ یک عدد معتبر برای روز بفرستید.")
                return True
            state.update(stage="price", days=days)
            await msg.reply_text("💵 قیمت را به تومان بفرستید.")
        else:
            price = _int(text)
            if price is None:
                await msg.reply_text("❗️ یک عدد معتبر بفرستید.")
                return True
            await db.add_plan(state["plan_name"], state["days"], price)
            ud.pop("state", None)
            await msg.reply_text("✅ سرویس اضافه شد.", reply_markup=Markup([[Btn("📦 سرویس‌ها", callback_data="adm:plans")]]))
    elif name == "adm_plan_price":
        price = _int(text)
        if price is None:
            await msg.reply_text("❗️ یک عدد معتبر بفرستید.")
            return True
        await db.set_plan_price(state["pid"], price)
        ud.pop("state", None)
        await msg.reply_text("✅ قیمت به‌روز شد.", reply_markup=Markup([[Btn("📦 سرویس‌ها", callback_data="adm:plans")]]))
    elif name == "adm_set":
        value = "" if text == "-" else text
        if state["key"] == "support_username":
            value = value.lstrip("@")
        await db.set_setting(state["key"], value)
        ud.pop("state", None)
        await msg.reply_text("✅ ذخیره شد.", reply_markup=Markup([[Btn("💳 تنظیمات", callback_data="adm:settings")]]))
    elif name == "adm_user_find":
        user = await db.find_user(text)
        if user is None:
            await msg.reply_text("❗️ کاربری با این مشخصات پیدا نشد (کاربر باید قبلاً ربات را استارت کرده باشد).")
            return True
        ud.pop("state", None)
        await _user_view(update, context, user)
    elif name == "adm_balance":
        amount = _int(text)
        if not amount:
            await msg.reply_text("❗️ یک عدد معتبر بفرستید.")
            return True
        delta = amount if state["sign"] == "+" else -amount
        new = await db.adjust_balance(state["uid"], delta, "admin_adjust",
                                      "افزایش توسط ادمین" if delta > 0 else "کاهش توسط ادمین")
        ud.pop("state", None)
        if new is None:
            await msg.reply_text("❌ انجام نشد (کاربر وجود ندارد یا موجودی کافی نیست).")
        else:
            await msg.reply_text(f"✅ انجام شد. موجودی جدید: {money(new)} تومان")
            try:
                await context.bot.send_message(
                    state["uid"], f"💰 موجودی حساب شما توسط ادمین {'افزایش' if delta > 0 else 'کاهش'} یافت.\n"
                                  f"موجودی فعلی: {money(new)} تومان")
            except TelegramError:
                pass
    elif name == "adm_extend":
        days = _int(text)
        if not days or days > 3650:
            await msg.reply_text("❗️ یک عدد معتبر برای روز بفرستید.")
            return True
        before = await db.get_service(state["sid"])
        await db.extend_service(state["sid"], days)
        ud.pop("state", None)
        if before is not None and before["status"] == "expired" and provisioner.enabled():
            context.application.create_task(autoinstall.run(context.bot, db, state["sid"]))
        await msg.reply_text(f"✅ {days} روز تمدید شد.", reply_markup=Markup([[Btn("🤖 سفارش", callback_data=f"adm:bot:{state['sid']}")]]))
    return True


def register(app: Application) -> None:
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))

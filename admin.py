from __future__ import annotations

import functools
import io
import json
import time

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

INSTALLED = ("active", "stopped")  # سرویس‌هایی که روی دیسک نصب شده‌اند
SECRET_KEYS = ("bot_token", "panel_password", "panel_api_token")


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
    services = [s for s in await db_of(context).recent_services(40) if s["status"] != "removed"]
    if not services:
        await _show(update, "🤖 هنوز سفارشی ثبت نشده است.", BACK)
        return
    rows = [[Btn(f"#{s['id']} {s['bot_name'] or s['plan_name']} · {status_label(s)}", callback_data=f"adm:bot:{s['id']}")]
            for s in services]
    rows.append([Btn("🔙 پنل مدیریت", callback_data="adm:home")])
    await _show(update, "🤖 <b>ربات‌های مشتریان</b> (۴۰ سفارش آخر)", Markup(rows))


async def _bot_view(update, context, sid: int, note: str = "") -> None:
    s = await db_of(context).get_service(sid)
    if s is None or s["status"] == "removed":
        await _bots_view(update, context)
        return
    live = ""
    if provisioner.enabled() and s["status"] in INSTALLED:
        ok, out = await provisioner.status(provisioner.slug_for(s))
        state = out.strip().splitlines()[-1] if out.strip() else "?"
        live = "\n🖥 روی سرور: " + ("🟢 در حال اجرا" if ok and state == "active" else f"🔴 {esc(state)}")
    text = (f"🤖 <b>سفارش #{sid}</b>\n\n👤 مشتری: <code>{s['user_id']}</code>\n"
            f"🔖 ربات: @{esc(s['bot_username'] or '—')}\n🏷 نام: {esc(s['bot_name'] or '—')}\n"
            f"📦 {esc(s['plan_name'])} · {money(s['price'])} تومان\n"
            f"📅 {fmt_date(s['start_at'])} تا {fmt_date(s['end_at'])} ({days_left(s)} روز مانده)\n"
            f"📍 {status_label(s)}{live}")
    if note:
        text += f"\n\n{note}"
    rows = []
    if s["status"] in ("pending_install", "installing", "active", "expired", "stopped"):
        rows.append([Btn("📄 فایل .env", callback_data=f"adm:env:{sid}")])
    if provisioner.enabled() and s["status"] in ("pending_install", "installing"):
        rows.append([Btn("🚀 نصب خودکار", callback_data=f"adm:botact:auto:{sid}")])
    if s["status"] in ("pending_install", "expired"):
        rows.append([Btn("⚙️ در حال نصب", callback_data=f"adm:botact:installing:{sid}")])
    if s["status"] in ("pending_install", "installing", "expired"):
        rows.append([Btn("✅ نصب شد (فعال)", callback_data=f"adm:botact:active:{sid}")])
    if s["status"] != "awaiting_info":
        rows.append([Btn("✏️ ویرایش اطلاعات", callback_data=f"adm:botedit:{sid}")])
    if provisioner.enabled() and s["status"] in INSTALLED:
        toggle = (Btn("▶️ شروع", callback_data=f"adm:botstart:{sid}") if s["status"] == "stopped"
                  else Btn("⏸ توقف", callback_data=f"adm:botstop:{sid}"))
        rows.append([toggle, Btn("🔄 ریستارت", callback_data=f"adm:botrs:{sid}"),
                     Btn("📜 لاگ", callback_data=f"adm:botlogs:{sid}")])
    if provisioner.enabled() and s["status"] != "awaiting_info":
        rows.append([Btn("🗑 حذف کامل", callback_data=f"adm:botdel:{sid}")])
    rows.append([Btn("➕ تمدید", callback_data=f"adm:extend:{sid}")])
    rows.append([Btn("🔙 لیست", callback_data="adm:bots")])
    await _show(update, text, Markup(rows))


# ------------------------------------------------------- ویرایش / مدیریت ربات فروخته‌شده
def _idata(s: dict) -> dict:
    try:
        return json.loads(s["install_data"] or "{}")
    except ValueError:
        return {}


def _show_value(step, data: dict) -> str:
    v = str(data.get(step.key, "") or "")
    if not v:
        return "—"
    if step.secret:
        return "••••"
    return v if len(v) <= 28 else v[:25] + "…"


async def _edit_fields_view(update, context, sid: int, note: str = "") -> None:
    s = await db_of(context).get_service(sid)
    if s is None or s["status"] in ("removed", "awaiting_info"):
        await _bots_view(update, context)
        return
    data = _idata(s)
    missing = set(flow.missing_required(data))
    rows = [[Btn(f"{'⚠️ ' if st.key in missing else ''}{st.title}: {_show_value(st, data)}",
                 callback_data=f"adm:botef:{sid}:{st.key}")] for st in flow.applicable(data)]
    rows.append([Btn("🔙 بازگشت", callback_data=f"adm:bot:{sid}")])
    text = (f"✏️ <b>ویرایش سفارش #{sid}</b> (@{esc(s['bot_username'] or '—')})\n\n"
            "فیلد موردنظر را انتخاب کنید. بعد از ذخیره، .env روی سرور بازنویسی و ربات ریستارت می‌شود.")
    if note:
        text = f"{note}\n\n{text}"
    await _show(update, text, Markup(rows))


async def _ask_field(update, context, sid: int, key: str) -> None:
    s = await db_of(context).get_service(sid)
    step = flow.get_step(key)
    if s is None or step is None:
        await _bots_view(update, context)
        return
    if s["status"] == "installing":
        await _bot_view(update, context, sid, "⏳ سفارش در حال نصب است؛ کمی بعد تلاش کنید.")
        return
    data = _idata(s)
    cur = "—" if not data.get(key) else ("••••" if step.secret else esc(str(data[key])))
    text = f"{step.prompt}\n\nمقدار فعلی: <code>{cur}</code>"
    rows = []
    if step.kind == "choice":
        context.user_data.pop("state", None)
        rows.append([Btn(label, callback_data=f"adm:botec:{sid}:{key}:{i}") for i, (label, _) in enumerate(step.choices)])
    else:
        context.user_data["state"] = {"name": "adm_bot_edit", "sid": sid, "key": key}
        text += "\n\n✍️ مقدار جدید را ارسال کنید."
    if step.optional and data.get(key):
        rows.append([Btn("🧹 پاک کردن مقدار", callback_data=f"adm:botclr:{sid}:{key}")])
    rows.append([Btn("❌ انصراف", callback_data=f"adm:botedit:{sid}")])
    await _show(update, text, Markup(rows))


async def _apply_edit(context, sid: int, key: str, value: str, extra: dict | None = None) -> tuple[bool, str]:
    db = db_of(context)
    s = await db.get_service(sid)
    if s is None:
        return False, "❌ سفارش پیدا نشد."
    data = _idata(s)
    if extra:
        data.update(extra)
    if value:
        data[key] = value
    else:
        data.pop(key, None)
    if not await db.update_install(sid, s["install_step"], data):
        return False, "❌ این توکن قبلاً برای سرویس دیگری ثبت شده است."
    missing = flow.missing_required(data)
    if missing:
        titles = ", ".join(esc(flow.get_step(k).title) for k in missing if flow.get_step(k))
        return True, f"✅ ذخیره شد، اما فیلدهای ضروری ناقص است: {titles}\nتا تکمیل آن‌ها روی سرور اعمال نمی‌شود."
    if s["status"] in INSTALLED and provisioner.enabled():
        env_text = flow.render_env(flow.build_env(data))
        ok, out = await provisioner.update_env(provisioner.slug_for(s), env_text)
        if not ok:
            return False, f"⚠️ ذخیره شد ولی اعمال روی سرور ناموفق بود:\n<pre>{esc(out[-800:])}</pre>"
        how = "ریستارت شد" if "restarted" in out else "بدون ریستارت، سرویس متوقف است"
        return True, f"✅ ذخیره و روی سرور اعمال شد ({how})."
    return True, "✅ ذخیره شد."


async def _bot_manage(update, context, act: str, sid: int) -> None:
    """توقف / شروع / ریستارت / لاگ / حذف کامل ربات روی سرور."""
    db = db_of(context)
    s = await db.get_service(sid)
    if s is None or s["status"] == "removed":
        await _bots_view(update, context)
        return
    if not provisioner.enabled():
        await _bot_view(update, context, sid, "❌ نصب خودکار فعال نیست (PROVISION_ENABLED).")
        return
    if s["status"] == "installing":
        await _bot_view(update, context, sid, "⏳ سفارش در حال نصب است؛ کمی بعد تلاش کنید.")
        return
    slug = provisioner.slug_for(s)

    if act == "botstop":
        ok, out = await provisioner.stop(slug)
        if ok:
            await db.set_service_status(sid, "stopped")
        await _bot_view(update, context, sid, "✅ ربات متوقف شد." if ok else f"❌ توقف ناموفق:\n<pre>{esc(out[-800:])}</pre>")
    elif act == "botstart":
        if s["end_at"] <= int(time.time()):
            await _bot_view(update, context, sid, "❌ سرویس منقضی شده است؛ ابتدا تمدید کنید.")
            return
        ok, out = await provisioner.start(slug)
        if ok:
            await db.set_service_status(sid, "active")
        await _bot_view(update, context, sid, "✅ ربات اجرا شد." if ok else f"❌ اجرا ناموفق:\n<pre>{esc(out[-800:])}</pre>")
    elif act == "botrs":
        ok, out = await provisioner.restart(slug)
        await _bot_view(update, context, sid, "✅ ریستارت شد." if ok else f"❌ ریستارت ناموفق:\n<pre>{esc(out[-800:])}</pre>")
    elif act == "botlogs":
        ok, out = await provisioner.logs(slug)
        await update.effective_chat.send_message(
            f"📜 <b>لاگ سفارش #{sid}</b>\n<pre>{esc(out[-3500:] or '-')}</pre>", parse_mode=ParseMode.HTML)
    elif act == "botdel":
        await _show(update, (
            f"🗑 <b>حذف کامل سفارش #{sid}</b> (@{esc(s['bot_username'] or '—')})\n\n"
            "سرویس متوقف و پوشه، دیتابیس و کاربر سیستمی این ربات برای همیشه از روی سرور پاک می‌شود.\n"
            "این کار قابل بازگشت نیست. مطمئنید؟"), Markup([
                [Btn("✅ بله، حذف شود", callback_data=f"adm:botdelok:{sid}")],
                [Btn("❌ انصراف", callback_data=f"adm:bot:{sid}")]]))
    elif act == "botdelok":
        ok, out = await provisioner.remove(slug)
        if not ok:
            await _bot_view(update, context, sid, f"❌ حذف ناموفق:\n<pre>{esc(out[-800:])}</pre>")
            return
        data = _idata(s)
        for k in SECRET_KEYS:
            data.pop(k, None)
        await db.update_install(sid, None, data)  # آزاد شدن توکن و پاک‌شدن رمزها از دیتابیس
        await db.set_service_status(sid, "removed")
        await _show(update, f"✅ ربات سفارش #{sid} به‌طور کامل حذف شد.",
                    Markup([[Btn("🔙 لیست", callback_data="adm:bots")]]))


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
    elif action in ("botstop", "botstart", "botrs", "botlogs", "botdel", "botdelok"):
        ud.pop("state", None)
        await _bot_manage(update, context, action, int(args[0]))
    elif action == "botedit":
        ud.pop("state", None)
        await _edit_fields_view(update, context, int(args[0]))
    elif action == "botef":
        await _ask_field(update, context, int(args[0]), args[1])
    elif action in ("botec", "botclr"):
        sid, key = int(args[0]), args[1]
        step = flow.get_step(key)
        value = None
        if step is not None:
            if action == "botclr" and step.optional:
                value = ""
            elif action == "botec":
                try:
                    value = step.choices[int(args[2])][1]
                except (IndexError, ValueError):
                    value = None
        if value is None:
            await _edit_fields_view(update, context, sid, "❌ مقدار نامعتبر.")
        else:
            _, note = await _apply_edit(context, sid, key, value)
            await _edit_fields_view(update, context, sid, note)
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
    elif name == "adm_bot_edit":
        sid, step = state["sid"], flow.get_step(state["key"])
        s = await db.get_service(sid)
        if s is None or step is None or s["status"] == "removed":
            ud.pop("state", None)
            await msg.reply_text("❌ سفارش یا فیلد پیدا نشد.", reply_markup=BACK)
            return True
        if step.secret:
            try:
                await msg.delete()
            except TelegramError:
                pass

        async def in_use(token: str) -> bool:
            return await db.token_in_use(token, sid)

        ctx = flow.Ctx(data=dict(_idata(s)), main_token=config.BOT_TOKEN, token_in_use=in_use)
        res = await step.validate(msg.text or "", ctx) if step.validate else flow.Result(value=text)
        if res.error and not res.soft:
            await update.effective_chat.send_message(
                f"❌ {esc(res.error)}\nدوباره بفرستید یا انصراف بزنید.", parse_mode=ParseMode.HTML,
                reply_markup=Markup([[Btn("❌ انصراف", callback_data=f"adm:botedit:{sid}")]]))
            return True
        ud.pop("state", None)
        extra = {k: ctx.data[k] for k in ("bot_id", "bot_username") if k in ctx.data} if step.key == "bot_token" else {}
        _, note = await _apply_edit(context, sid, step.key, res.value, extra)
        if res.error:  # خطای نرم، مثلاً ادمین‌نبودن ربات در کانال
            note += f"\n⚠️ {esc(res.error)}"
        await update.effective_chat.send_message(
            note, parse_mode=ParseMode.HTML,
            reply_markup=Markup([[Btn("✏️ ادامه‌ی ویرایش", callback_data=f"adm:botedit:{sid}")],
                                 [Btn("🤖 سفارش", callback_data=f"adm:bot:{sid}")]]))
    return True


def register(app: Application) -> None:
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern=r"^adm:"))
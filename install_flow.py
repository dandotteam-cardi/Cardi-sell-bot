"""
مراحل دریافت اطلاعات نصب ربات مشتری.

فهرست پرسش‌ها و اعتبارسنجی‌ها دقیقاً از install.sh پروژه‌ی ربات فروش کانفیگ گرفته شده
و build_env() همان کلیدهای .env را می‌سازد که install.sh با تابع put می‌نویسد.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from telegram import Bot
from telegram.error import TelegramError

TOKEN_RE = re.compile(r"^[0-9]{6,}:[A-Za-z0-9_-]{30,}$")
CHANNEL_RE = re.compile(r"^(@[A-Za-z][A-Za-z0-9_]{4,31}|-100[0-9]{5,})$")
FORCE_SUB_RE = re.compile(r"^-100[0-9]{5,}(,-100[0-9]{5,})*$")
ADMINS_RE = re.compile(r"^[0-9]+(,[0-9]+)*$")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
URL_RE = re.compile(r"^https?://[^\s]+$")
INBOUNDS_RE = re.compile(r"^[0-9]+(,[0-9]+)*$")


@dataclass
class Result:
    error: str | None = None
    value: str = ""
    soft: bool = False  # خطای قابل‌عبور (مثلاً بررسی ادمین بودن ربات در کانال)


@dataclass
class Ctx:
    data: dict
    main_token: str
    token_in_use: Callable[[str], Awaitable[bool]]


Validator = Callable[[str, Ctx], Awaitable[Result]]


@dataclass
class Step:
    key: str
    title: str
    prompt: str
    validate: Validator | None = None
    kind: str = "text"  # text | choice
    choices: tuple[tuple[str, str], ...] = ()
    optional: bool = False
    secret: bool = False
    me_button: bool = False
    when: Callable[[dict], bool] | None = None


# ------------------------------------------------------------ validators
async def v_token(raw: str, ctx: Ctx) -> Result:
    token = raw.strip()
    if not TOKEN_RE.match(token):
        return Result(error="فرمت توکن نامعتبر است. نمونه: 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    if token == ctx.main_token:
        return Result(error="این توکن مربوط به همین ربات فروش است. یک ربات جدید از @BotFather بسازید.")
    if await ctx.token_in_use(token):
        return Result(error="این توکن قبلاً برای یک سرویس دیگر ثبت شده است.")
    try:
        async with Bot(token) as bot:
            me = await bot.get_me()
    except TelegramError:
        return Result(error="تلگرام این توکن را تأیید نکرد. توکن را دوباره از @BotFather کپی کنید.")
    ctx.data["bot_id"] = me.id
    ctx.data["bot_username"] = me.username or ""
    return Result(value=token)


async def v_name(raw: str, ctx: Ctx) -> Result:
    name = " ".join(raw.split())
    if not 2 <= len(name) <= 64:
        return Result(error="نام باید بین ۲ تا ۶۴ کاراکتر باشد.")
    return Result(value=name)


async def v_admins(raw: str, ctx: Ctx) -> Result:
    value = re.sub(r"\s+", "", raw)
    if not ADMINS_RE.match(value):
        return Result(error="فقط عدد و ویرگول مجاز است. نمونه: 123456789,987654321 (آیدی عددی را از @userinfobot بگیرید)")
    return Result(value=",".join(dict.fromkeys(value.split(","))))


async def _verify_channel(channel: str, ctx: Ctx) -> Result:
    token, bot_id = ctx.data.get("bot_token"), ctx.data.get("bot_id")
    try:
        async with Bot(token) as bot:
            member = await bot.get_chat_member(channel, bot_id)
    except TelegramError:
        return Result(
            error="ربات شما در این کانال پیدا نشد. ربات را در کانال ادمین کنید و دوباره بفرستید.",
            value=channel, soft=True,
        )
    if member.status not in ("administrator", "creator"):
        return Result(
            error="ربات شما در این کانال ادمین نیست. آن را ادمین کنید و دوباره بفرستید.",
            value=channel, soft=True,
        )
    return Result(value=channel)


async def v_channel(raw: str, ctx: Ctx) -> Result:
    value = raw.strip()
    if not CHANNEL_RE.match(value):
        return Result(error="آیدی کانال باید به شکل @username یا عددی مثل -1001234567890 باشد.")
    return await _verify_channel(value, ctx)


async def v_force_sub(raw: str, ctx: Ctx) -> Result:
    value = re.sub(r"\s+", "", raw)
    if not FORCE_SUB_RE.match(value):
        return Result(error="آیدی‌های عددی کانال‌ها را با ویرگول بفرستید. نمونه: -1001234567890,-1009876543210")
    return Result(value=value)


async def v_username(raw: str, ctx: Ctx) -> Result:
    value = raw.strip().lstrip("@")
    if not USERNAME_RE.match(value):
        return Result(error="یوزرنیم نامعتبر است (بدون @ و حداقل ۵ کاراکتر).")
    return Result(value=value)


async def v_url(raw: str, ctx: Ctx) -> Result:
    value = raw.strip().rstrip("/")
    if not URL_RE.match(value):
        return Result(error="آدرس باید با http:// یا https:// شروع شود.")
    return Result(value=value)


async def v_inbounds(raw: str, ctx: Ctx) -> Result:
    value = re.sub(r"\s+", "", raw)
    if not INBOUNDS_RE.match(value):
        return Result(error="فقط عدد بفرستید؛ برای چند اینباند با ویرگول جدا کنید. نمونه: 1,2")
    return Result(value=value)


async def v_nonempty(raw: str, ctx: Ctx) -> Result:
    value = raw.strip()
    if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        return Result(error="مقدار نامعتبر است.")
    return Result(value=value)


async def v_card(raw: str, ctx: Ctx) -> Result:
    value = " ".join(raw.split())
    if not value or len(value) > 200:
        return Result(error="حداکثر ۲۰۰ کاراکتر.")
    return Result(value=value)


# ------------------------------------------------------------ conditions
def _panel(d: dict) -> bool:
    return d.get("panel_connect") == "yes"


def _3x(d: dict) -> bool:
    return _panel(d) and d.get("panel_type") == "3xui"


def _modern(d: dict) -> bool:
    return _3x(d) and d.get("panel_api_mode") == "modern"


def _basic_auth(d: dict) -> bool:
    return _panel(d) and not _modern(d)


def _https(d: dict) -> bool:
    return _panel(d) and str(d.get("panel_url", "")).startswith("https://")


def _advanced(d: dict) -> bool:
    return d.get("advanced") == "yes"


CHANNEL_HINT = "ربات شما باید در این کانال ادمین باشد. آیدی عددی (مثل -1001234567890) یا @username بفرستید."

STEPS: tuple[Step, ...] = (
    Step("bot_token", "توکن", "🔑 <b>توکن ربات خود را ارسال کنید.</b>\n\n"
         "<blockquote>ربات را از @BotFather بسازید و توکن را اینجا بفرستید. پیام شما بعد از دریافت پاک می‌شود.</blockquote>",
         v_token, secret=True),
    Step("store_name", "نام ربات", "🏷 <b>نام ربات را وارد کنید.</b>\n\n"
         "<blockquote>این نام در متن‌های ربات فروش شما نمایش داده می‌شود.</blockquote>", v_name),
    Step("admins", "ادمین‌ها", "👮 <b>آیدی عددی ادمین‌ها را بفرستید.</b>\n\n"
         "<blockquote>با ویرگول جدا کنید؛ اولی ادمین اصلی است. آیدی عددی را از @userinfobot بگیرید.</blockquote>",
         v_admins, me_button=True),
    Step("source_channel", "کانال کانفیگ", "📢 <b>کانال منبع کانفیگ‌ها (SOURCE_CHANNEL)</b>\n\n"
         f"<blockquote>{CHANNEL_HINT}</blockquote>", v_channel),
    Step("data_channel", "کانال ذخیره‌ی محتوا", "🗄 <b>کانال ذخیره‌ی محتوای ادمین (DATA_CHANNEL)</b>\n\n"
         f"<blockquote>{CHANNEL_HINT}</blockquote>", v_channel),
    Step("log_channel", "کانال لاگ", "🧾 <b>کانال لاگ (LOG_CHANNEL)</b>\n\n"
         f"<blockquote>{CHANNEL_HINT}</blockquote>", v_channel),
    Step("support_username", "پشتیبانی", "💬 <b>یوزرنیم پشتیبانی را بفرستید (بدون @).</b>\n\n"
         "<blockquote>اختیاری است.</blockquote>", v_username, optional=True),
    Step("panel_connect", "اتصال پنل", "🔌 <b>می‌خواهید ربات هم‌اکنون به پنل (Marzban یا 3x-ui) وصل شود؟</b>\n\n"
         "<blockquote>اگر «بعداً» را بزنید، اتصال از داخل ربات قابل انجام است.</blockquote>",
         kind="choice", choices=(("✅ اتصال الان", "yes"), ("⏭ بعداً", "no"))),
    Step("panel_type", "نوع پنل", "🧩 <b>نوع پنل را انتخاب کنید.</b>", kind="choice",
         choices=(("Marzban", "marzban"), ("3x-ui", "3xui")), when=_panel),
    Step("panel_url", "آدرس پنل", "🌐 <b>آدرس پنل را بفرستید.</b>\n\n"
         "<blockquote>نمونه: https://panel.example.com:8000</blockquote>", v_url, when=_panel),
    Step("panel_api_mode", "حالت API", "⚙️ <b>حالت API پنل 3x-ui</b>\n\n"
         "<blockquote>legacy: 3x-ui معمولی با نام‌کاربری/رمز\nmodern: 3x-ui نسخه‌ی ۳ با توکن</blockquote>",
         kind="choice", choices=(("legacy", "legacy"), ("modern", "modern")), when=_3x),
    Step("panel_api_token", "توکن API پنل", "🔐 <b>توکن API پنل را بفرستید.</b>\n\n"
         "<blockquote>پیام شما بعد از دریافت پاک می‌شود.</blockquote>", v_nonempty, secret=True, when=_modern),
    Step("panel_username", "نام‌کاربری پنل", "👤 <b>نام‌کاربری ادمین پنل را بفرستید.</b>", v_nonempty, when=_basic_auth),
    Step("panel_password", "رمز پنل", "🔐 <b>رمز ادمین پنل را بفرستید.</b>\n\n"
         "<blockquote>پیام شما بعد از دریافت پاک می‌شود.</blockquote>", v_nonempty, secret=True, when=_basic_auth),
    Step("panel_inbounds", "اینباندها", "🔢 <b>شماره‌ی اینباند(ها) را بفرستید.</b>\n\n"
         "<blockquote>برای چند اینباند با ویرگول جدا کنید. نمونه: 1,2</blockquote>", v_inbounds, when=_3x),
    Step("panel_sub_base", "آدرس سابسکریپشن", "🔗 <b>آدرس پایه‌ی سابسکریپشن</b>\n\n"
         "<blockquote>اختیاری است؛ خالی یعنی خودکار.</blockquote>", v_url, optional=True, when=_panel),
    Step("panel_verify_ssl", "گواهی SSL", "🔒 <b>گواهی SSL پنل معتبر است؟</b>\n\n"
         "<blockquote>اگر self-signed است «نامعتبر» را بزنید.</blockquote>", kind="choice",
         choices=(("✅ معتبر", "1"), ("⚠️ نامعتبر", "0")), when=_https),
    Step("advanced", "تنظیمات پیشرفته", "🛠 <b>تنظیمات پیشرفته را الان وارد می‌کنید؟</b>\n\n"
         "<blockquote>عضویت اجباری، چرخ شانس، رفرال، لینک آموزش و شماره کارت. بقیه‌ی موارد مقدار پیش‌فرض می‌گیرند.</blockquote>",
         kind="choice", choices=(("✅ بله", "yes"), ("⏭ نه، پیش‌فرض", "no"))),
    Step("force_sub", "عضویت اجباری", "📌 <b>آیدی عددی کانال‌های عضویت اجباری</b>\n\n"
         "<blockquote>با ویرگول جدا کنید. اختیاری.</blockquote>", v_force_sub, optional=True, when=_advanced),
    Step("wheel_channel", "کانال چرخ شانس", "🎡 <b>کانال چرخ شانس (WHEEL_CHANNEL)</b>\n\n"
         f"<blockquote>{CHANNEL_HINT} اختیاری.</blockquote>", v_channel, optional=True, when=_advanced),
    Step("referral_channel", "کانال رفرال", "🤝 <b>کانال رفرال (REFERRAL_CHANNEL)</b>\n\n"
         f"<blockquote>{CHANNEL_HINT} اختیاری.</blockquote>", v_channel, optional=True, when=_advanced),
    Step("tutorial_link", "لینک آموزش", "📚 <b>لینک آموزش اتصال</b>\n\n<blockquote>اختیاری.</blockquote>",
         v_url, optional=True, when=_advanced),
    Step("support_address", "شماره کارت", "💳 <b>شماره کارت / آدرس پرداخت (SUPPORT_ADDRESS)</b>\n\n"
         "<blockquote>اختیاری.</blockquote>", v_card, optional=True, when=_advanced),
)

_BY_KEY = {s.key: s for s in STEPS}
FIRST_STEP = STEPS[0].key


def get_step(key: str | None) -> Step | None:
    return _BY_KEY.get(key or "")


def applicable(data: dict) -> list[Step]:
    return [s for s in STEPS if s.when is None or s.when(data)]


def next_step(data: dict, after_key: str | None) -> Step | None:
    steps = applicable(data)
    if after_key is None:
        return steps[0] if steps else None
    keys = [s.key for s in steps]
    if after_key in keys:
        idx = keys.index(after_key) + 1
    else:  # کلیدی که دیگر مرتبط نیست؛ اولین مرحله‌ی بعدی در ترتیب اصلی
        order = [s.key for s in STEPS]
        idx = next((i for i, k in enumerate(keys) if order.index(k) > order.index(after_key)), len(keys))
    return steps[idx] if idx < len(steps) else None


def prev_step(data: dict, key: str) -> Step | None:
    steps = applicable(data)
    if key == "confirm":
        return steps[-1] if steps else None
    keys = [s.key for s in steps]
    if key not in keys or keys.index(key) == 0:
        return None
    return steps[keys.index(key) - 1]


def position(data: dict, key: str) -> tuple[int, int]:
    keys = [s.key for s in applicable(data)]
    return (keys.index(key) + 1 if key in keys else len(keys)), len(keys)


# ------------------------------------------------------------ .env
def _quote(value: str) -> str:
    value = "".join(" " if ord(c) < 32 else c for c in str(value))  # جلوگیری از تزریق خط جدید در .env
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_env(d: dict) -> dict[str, str]:
    """
    همان کلیدهایی که install.sh در .env می‌نویسد (مقدارهای خالی نوشته نمی‌شوند).
    فقط مراحلی که در مسیر انتخاب‌شده فعال بوده‌اند خوانده می‌شوند.
    """
    admins = [a for a in str(d.get("admins", "")).split(",") if a]
    env: dict[str, str] = {
        "BOT_TOKEN": d.get("bot_token", ""),
        "ADDMIN_BOT_NUMID": admins[0] if admins else "",
        "ADMIN_USER_IDS": ",".join(admins[1:]),
        "STORE_NAME": d.get("store_name", ""),
        "SOURCE_CHANNEL": d.get("source_channel", ""),
        "DATA_CHANNEL": d.get("data_channel", ""),
        "LOG_CHANNEL": d.get("log_channel", ""),
        "SUPPORT_USERNAME": d.get("support_username", ""),
    }
    if _panel(d):
        env["PANEL_TYPE"] = d.get("panel_type", "")
        env["PANEL_URL"] = d.get("panel_url", "")
        if _basic_auth(d):
            env["PANEL_USERNAME"] = d.get("panel_username", "")
            env["PANEL_PASSWORD"] = d.get("panel_password", "")
        env["PANEL_SUB_BASE"] = d.get("panel_sub_base", "")
        if _https(d):
            env["PANEL_VERIFY_SSL"] = d.get("panel_verify_ssl", "1")
        if _3x(d):
            env["PANEL_API_MODE"] = d.get("panel_api_mode", "legacy")
            if _modern(d):
                env["PANEL_API_TOKEN"] = d.get("panel_api_token", "")
            inb = d.get("panel_inbounds", "1")
            env["PANEL_INBOUND_IDS" if "," in inb else "PANEL_INBOUND_ID"] = inb
    if _advanced(d):
        env["FORCE_SUB_CHANNELS"] = d.get("force_sub", "")
        env["WHEEL_CHANNEL"] = d.get("wheel_channel", "")
        env["REFERRAL_CHANNEL"] = d.get("referral_channel", "")
        env["CONNECTION_TUTORIAL_LINK"] = d.get("tutorial_link", "")
        env["SUPPORT_ADDRESS"] = d.get("support_address", "")
    return {k: v for k, v in env.items() if v}


def render_env(env: dict[str, str]) -> str:
    return "".join(f"{k}={_quote(v)}\n" for k, v in env.items())


def missing_required(d: dict) -> list[str]:
    return [s.key for s in applicable(d) if not s.optional and not d.get(s.key)]
# ربات فروش ربات (Telegram)

ربات تلگرامی برای فروش سرویس ربات: کیف پول (شارژ با رسید و تایید ادمین)، خرید پلن، دریافت مرحله‌به‌مرحله‌ی اطلاعات نصب از مشتری، پنل مدیریت برای ادمین و یادآوری/انقضای خودکار.

## نصب سریع روی سرور (Ubuntu/Debian)

سرور باید به تلگرام دسترسی داشته باشد (ترجیحاً سرور خارج از ایران) و پایتون ۳.۱۰ یا بالاتر داشته باشد. دستور زیر را با کاربر root اجرا کنید (`sudo -i`):

```bash
REPO_URL=https://github.com/dandotteam-cardi/Cardi-sell-bot.git bash <(curl -fsSL https://raw.githubusercontent.com/dandotteam-cardi/Cardi-sell-bot/main/install.sh)
```

اسکریپت توکن ربات و آیدی عددی ادمین‌ها را می‌پرسد، سورس را در `/opt/seller-bot` نصب می‌کند و ربات را به‌صورت سرویس `seller-bot` اجرا می‌کند (بعد از ریبوت هم بالا می‌آید). همچنین دستور مدیریتی `sellerbot` ساخته می‌شود.

## نصب دستی (با کلون ریپو)

```bash
git clone https://github.com/dandotteam-cardi/Cardi-sell-bot.git
cd Cardi-sell-bot
sudo REPO_URL=https://github.com/dandotteam-cardi/Cardi-sell-bot.git bash install.sh
```

## به‌روزرسانی

```bash
sudo sellerbot update
```

فایل `.env` و دیتابیس دست نمی‌خورند.

## دستورهای مدیریتی

```bash
sudo sellerbot status       # وضعیت ربات
sudo sellerbot logs         # لاگ زنده
sudo sellerbot restart      # راه‌اندازی مجدد
sudo sellerbot stop         # توقف
sudo sellerbot start        # اجرا
sudo sellerbot edit         # ویرایش .env (بعد از ذخیره ربات خودکار ریستارت می‌شود)
sudo sellerbot update       # به‌روزرسانی به آخرین نسخه
sudo sellerbot uninstall    # حذف
```

معادل با systemd:

```bash
journalctl -u seller-bot -f          # لاگ زنده
systemctl restart seller-bot         # راه‌اندازی مجدد
systemctl stop seller-bot            # توقف
nano /opt/seller-bot/.env            # تنظیمات (بعد از تغییر، ربات را ریستارت کنید)
```

## استفاده

- ادمین در ربات دستور `/admin` را بزند: تعریف سرویس‌ها و قیمت‌ها، شماره کارت، کاربران، سفارش‌ها و آمار.
- مشتری از «🛒 خرید سرویس» خرید می‌کند و اطلاعات ربات خودش را مرحله‌به‌مرحله وارد می‌کند.
- سفارش در پنل ادمین (🤖 ربات‌های مشتریان) دیده می‌شود. ادمین فایل `.env` آماده‌ی ربات مشتری را دریافت می‌کند، ربات را نصب می‌کند و وضعیت را روی «نصب شد (فعال)» می‌گذارد.

## نکته‌ی امنیتی

فایل `.env` شامل توکن است و در گیت قرار نمی‌گیرد (در `.gitignore` است). توکن را هیچ‌جا منتشر نکنید.
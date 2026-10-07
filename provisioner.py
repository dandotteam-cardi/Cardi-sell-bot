from __future__ import annotations

import asyncio
import os
import re

import config

HELPER = "/usr/local/sbin/sellbot-provision"
INSTALL_TIMEOUT = 900
TIMEOUT = 90

# اسلاگ = <آیدی عددی تلگرام مشتری>-<شماره سفارش>   مثال: 123456789-42
_SLUG = re.compile(r"^[0-9]{1,20}-[0-9]{1,12}$")


def enabled() -> bool:
    """نصب خودکار فقط وقتی فعال است که PROVISION_ENABLED=1 باشد و اسکریپت کمکی نصب شده باشد."""
    return config.PROVISION_ENABLED and os.path.exists(HELPER)


def slug_for(service) -> str:
    """نام پوشه/سرویس ربات مشتری: <user_id>-<service_id>"""
    return f"{int(service['user_id'])}-{int(service['id'])}"


async def _run(action: str, slug: str, stdin_text: str | None = None,
               timeout: int = TIMEOUT) -> tuple[bool, str]:
    if not _SLUG.match(slug or ""):
        return False, "bad-slug"
    if not os.path.exists(HELPER):
        return False, "not-installed"
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", HELPER, action, slug,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(
                proc.communicate(stdin_text.encode() if stdin_text is not None else None),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"error: {exc}"
    text = out.decode("utf-8", "replace").strip()
    return proc.returncode == 0, text[-1500:]


async def install(slug: str, env_text: str) -> tuple[bool, str]:
    """ساخت پوشه‌ی مشتری، کلون ریپوی ربات، venv، نوشتن .env و راه‌اندازی سرویس systemd."""
    return await _run("install", slug, stdin_text=env_text, timeout=INSTALL_TIMEOUT)


async def start(slug: str) -> tuple[bool, str]:
    return await _run("start", slug)


async def stop(slug: str) -> tuple[bool, str]:
    return await _run("stop", slug)


async def remove(slug: str) -> tuple[bool, str]:
    """توقف سرویس و حذف کامل پوشه و کاربر سیستمی ربات مشتری."""
    return await _run("remove", slug)

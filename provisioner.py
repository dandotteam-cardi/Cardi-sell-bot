from __future__ import annotations

import asyncio
import shutil

import config

HELPER = "/usr/local/bin/sellbot-provision"
TIMEOUT = 60


def enabled() -> bool:
    """نصب/توقف خودکار ربات مشتری‌ها فقط وقتی فعال است که PROVISION_ENABLED=1 باشد."""
    return config.PROVISION_ENABLED


async def _run(action: str, service_id: int) -> tuple[bool, str]:
    if not shutil.which(HELPER) and not shutil.os.path.exists(HELPER):
        return False, "not-installed"
    try:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-n", HELPER, action, str(int(service_id)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False, "timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"error: {exc}"
    text = out.decode("utf-8", "replace").strip()
    return proc.returncode == 0, text


async def stop(service_id: int) -> tuple[bool, str]:
    return await _run("stop", service_id)


async def start(service_id: int) -> tuple[bool, str]:
    return await _run("start", service_id)

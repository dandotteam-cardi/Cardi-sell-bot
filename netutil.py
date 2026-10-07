"""تنظیمات اتصال شبکه به تلگرام (پروکسی، IPv4 اجباری، تایم‌اوت)."""
from __future__ import annotations

import httpx
from telegram.request import HTTPXRequest

import config


def make_request() -> HTTPXRequest:
    kwargs = dict(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    if config.PROXY_URL:
        return HTTPXRequest(proxy=config.PROXY_URL, **kwargs)
    if config.FORCE_IPV4:
        # بسیاری از شبکه‌ها به آدرس IPv6 تلگرام وصل نمی‌شوند؛ اتصال را روی IPv4 می‌بندیم.
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
        return HTTPXRequest(httpx_kwargs={"transport": transport}, **kwargs)
    return HTTPXRequest(**kwargs)

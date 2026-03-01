"""Timezone helpers (Asia/Shanghai)."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

BJ_TZ = ZoneInfo("Asia/Shanghai")


def now_bj() -> dt.datetime:
    return dt.datetime.now(BJ_TZ)


def format_bj_now(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return now_bj().strftime(fmt)


def naive_utc_to_bj(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone(BJ_TZ)

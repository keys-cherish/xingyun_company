"""Tests for Mini App signature and session token security helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from urllib.parse import urlencode

from api.security import (
    MiniAppAuthError,
    issue_session_token,
    verify_session_token,
    verify_telegram_init_data,
)


def _build_init_data(bot_token: str, user_id: int, auth_date: int) -> str:
    user_payload = {
        "id": user_id,
        "first_name": "Mini",
        "last_name": "User",
        "username": "mini_user",
    }
    data = {
        "auth_date": str(auth_date),
        "query_id": "AAH-test-query-id",
        "user": json.dumps(user_payload, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data_hash = hmac.new(secret, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    payload = {**data, "hash": data_hash}
    return urlencode(payload)


def test_verify_telegram_init_data_success():
    bot_token = "12345:secure-token"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    init_data = _build_init_data(bot_token, user_id=42, auth_date=now_ts)

    identity = verify_telegram_init_data(init_data, bot_token, max_age_seconds=300)
    assert identity.tg_id == 42


def test_verify_telegram_init_data_rejects_tamper():
    bot_token = "12345:secure-token"
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    init_data = _build_init_data(bot_token, user_id=42, auth_date=now_ts)
    tampered = init_data.replace("mini_user", "evil_user")

    try:
        verify_telegram_init_data(tampered, bot_token, max_age_seconds=300)
    except MiniAppAuthError:
        pass
    else:
        raise AssertionError("tampered init data must be rejected")


def test_verify_telegram_init_data_rejects_expired():
    bot_token = "12345:secure-token"
    old_ts = int(dt.datetime.now(dt.UTC).timestamp()) - 3600
    init_data = _build_init_data(bot_token, user_id=42, auth_date=old_ts)

    try:
        verify_telegram_init_data(init_data, bot_token, max_age_seconds=300)
    except MiniAppAuthError:
        pass
    else:
        raise AssertionError("expired init data must be rejected")


def test_session_token_roundtrip():
    bot_token = "12345:secure-token"
    token = issue_session_token(42, bot_token, ttl_seconds=600)
    session = verify_session_token(token, bot_token)
    assert session.tg_id == 42


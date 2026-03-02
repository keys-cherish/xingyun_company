"""Telegram Mini App security helpers."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    """Raised when Mini App auth payload is invalid."""


@dataclass(slots=True, frozen=True)
class MiniAppIdentity:
    """Verified Telegram Mini App user identity."""

    tg_id: int
    auth_date: int
    user: dict[str, Any]


@dataclass(slots=True, frozen=True)
class MiniAppSession:
    """Parsed Mini App session token payload."""

    tg_id: int
    iat: int
    exp: int


def _urlsafe_b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _urlsafe_b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("ascii"))


def _derive_webapp_secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _derive_session_secret(bot_token: str) -> bytes:
    return hmac.new(b"MiniAppSession", bot_token.encode("utf-8"), hashlib.sha256).digest()


def _build_data_check_string(items: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in sorted(items.items(), key=lambda x: x[0]))


def _now_ts() -> int:
    return int(dt.datetime.now(dt.UTC).timestamp())


def verify_telegram_init_data(
    init_data_raw: str,
    bot_token: str,
    max_age_seconds: int,
) -> MiniAppIdentity:
    """Verify Telegram Mini App initData signature and freshness."""

    if not bot_token:
        raise MiniAppAuthError("BOT_TOKEN is empty")
    if not init_data_raw:
        raise MiniAppAuthError("init_data is empty")

    pairs = parse_qsl(init_data_raw, keep_blank_values=True)
    if not pairs:
        raise MiniAppAuthError("init_data has no key-value pairs")

    data: dict[str, str] = {}
    for key, value in pairs:
        data[key] = value

    received_hash = data.pop("hash", "")
    if not received_hash:
        raise MiniAppAuthError("init_data hash is missing")

    expected_hash = hmac.new(
        _derive_webapp_secret(bot_token),
        _build_data_check_string(data).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise MiniAppAuthError("init_data signature mismatch")

    try:
        auth_date = int(data.get("auth_date", "0"))
    except ValueError as exc:
        raise MiniAppAuthError("auth_date is invalid") from exc
    if auth_date <= 0:
        raise MiniAppAuthError("auth_date is missing")

    max_age = max(0, int(max_age_seconds))
    now_ts = _now_ts()
    if max_age > 0 and (now_ts - auth_date) > max_age:
        raise MiniAppAuthError("init_data is expired")

    user_raw = data.get("user")
    if not user_raw:
        raise MiniAppAuthError("user payload is missing")
    try:
        user_payload = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise MiniAppAuthError("user payload is invalid json") from exc

    try:
        tg_id = int(user_payload["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MiniAppAuthError("user.id is invalid") from exc
    if tg_id <= 0:
        raise MiniAppAuthError("user.id is invalid")

    return MiniAppIdentity(tg_id=tg_id, auth_date=auth_date, user=user_payload)


def issue_session_token(tg_id: int, bot_token: str, ttl_seconds: int) -> str:
    """Issue a signed, short-lived session token for Mini App API calls."""

    now_ts = _now_ts()
    ttl = max(60, int(ttl_seconds))
    payload = {
        "tg_id": int(tg_id),
        "iat": now_ts,
        "exp": now_ts + ttl,
    }
    body = _urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        _derive_session_secret(bot_token),
        body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{body}.{_urlsafe_b64encode(signature)}"


def verify_session_token(token: str, bot_token: str) -> MiniAppSession:
    """Validate Mini App session token and return payload."""

    if not token:
        raise MiniAppAuthError("session token is empty")
    parts = token.split(".", 1)
    if len(parts) != 2:
        raise MiniAppAuthError("session token format is invalid")
    body, sig = parts
    expected_sig = hmac.new(
        _derive_session_secret(bot_token),
        body.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    try:
        received_sig = _urlsafe_b64decode(sig)
    except Exception as exc:  # noqa: BLE001
        raise MiniAppAuthError("session token signature is invalid") from exc
    if not hmac.compare_digest(expected_sig, received_sig):
        raise MiniAppAuthError("session token signature mismatch")

    try:
        payload = json.loads(_urlsafe_b64decode(body).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise MiniAppAuthError("session token payload is invalid") from exc

    try:
        tg_id = int(payload["tg_id"])
        iat = int(payload["iat"])
        exp = int(payload["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MiniAppAuthError("session token payload fields are invalid") from exc

    now_ts = _now_ts()
    if exp <= now_ts:
        raise MiniAppAuthError("session token is expired")
    if tg_id <= 0 or iat <= 0 or exp <= iat:
        raise MiniAppAuthError("session token payload values are invalid")

    return MiniAppSession(tg_id=tg_id, iat=iat, exp=exp)


def parse_bearer_token(authorization: str | None) -> str:
    """Extract bearer token from Authorization header."""

    if not authorization:
        raise MiniAppAuthError("authorization header is missing")
    prefix = "bearer "
    if not authorization.lower().startswith(prefix):
        raise MiniAppAuthError("authorization header must use bearer token")
    token = authorization[len(prefix):].strip()
    if not token:
        raise MiniAppAuthError("bearer token is empty")
    return token


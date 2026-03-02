"""Litestar routes for Mini App API."""

from __future__ import annotations

from typing import Any

from litestar import Request, get, post
from litestar.exceptions import HTTPException

from api.preload import ensure_user_exists, load_preload_data
from api.security import (
    MiniAppAuthError,
    issue_session_token,
    parse_bearer_token,
    verify_session_token,
    verify_telegram_init_data,
)
from config import settings


def _parse_optional_company_id(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        cid = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="company_id must be an integer") from exc
    if cid <= 0:
        raise HTTPException(status_code=400, detail="company_id must be positive")
    return cid


def _extract_user_name(user_payload: dict[str, Any]) -> str:
    first_name = str(user_payload.get("first_name", "")).strip()
    last_name = str(user_payload.get("last_name", "")).strip()
    full_name = f"{first_name} {last_name}".strip()
    if full_name:
        return full_name
    username = str(user_payload.get("username", "")).strip()
    if username:
        return username
    return str(user_payload.get("id", "unknown"))


@get("/api/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "miniapp-api"}


@post("/api/miniapp/auth")
async def miniapp_auth(data: dict[str, Any]) -> dict[str, Any]:
    """Verify Telegram initData and return API session + preload."""
    init_data = str(data.get("init_data", "") or "")
    company_id = _parse_optional_company_id(data.get("company_id"))
    try:
        identity = verify_telegram_init_data(
            init_data,
            settings.bot_token,
            settings.miniapp_auth_max_age_seconds,
        )
    except MiniAppAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    await ensure_user_exists(identity.tg_id, _extract_user_name(identity.user))
    session_token = issue_session_token(
        identity.tg_id,
        settings.bot_token,
        settings.miniapp_session_ttl_seconds,
    )
    preload = await load_preload_data(
        identity.tg_id,
        company_id,
        settings.miniapp_preload_ttl_seconds,
    )
    return {
        "session_token": session_token,
        "expires_in": settings.miniapp_session_ttl_seconds,
        "preload": preload,
    }


@get("/api/miniapp/preload")
async def miniapp_preload(request: Request, company_id: int | None = None) -> dict[str, Any]:
    """Return preload snapshot for current Mini App session."""
    try:
        token = parse_bearer_token(request.headers.get("authorization"))
        session = verify_session_token(token, settings.bot_token)
    except MiniAppAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return await load_preload_data(
        session.tg_id,
        company_id,
        settings.miniapp_preload_ttl_seconds,
    )


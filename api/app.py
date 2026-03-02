"""Litestar application entrypoint."""

from __future__ import annotations

from litestar import Litestar
from litestar.config.cors import CORSConfig

from api.routes import healthz, miniapp_auth, miniapp_preload
from config import settings


def _build_cors_config() -> CORSConfig | None:
    origins = [
        origin.strip()
        for origin in settings.miniapp_allowed_origins.split(",")
        if origin.strip()
    ]
    if not origins:
        return None
    return CORSConfig(
        allow_origins=origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=True,
        max_age=600,
    )


app = Litestar(
    route_handlers=[healthz, miniapp_auth, miniapp_preload],
    cors_config=_build_cors_config(),
)


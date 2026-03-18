"""Run Mini App API server (with uvloop when available)."""

from __future__ import annotations

import uvicorn

from config import settings
from utils.logging_setup import setup_logging


def main() -> None:
    setup_logging("api")

    # uvloop: uvicorn natively supports "uvloop" as loop option.
    # When USE_UVLOOP=true and the package is installed, uvicorn
    # will use uvloop automatically; otherwise falls back to asyncio.
    loop_policy = "auto"
    if settings.use_uvloop:
        try:
            import uvloop  # noqa: F401

            loop_policy = "uvloop"
        except ImportError:
            pass

    uvicorn.run(
        "api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=1,  # single worker is generally safer on small servers.
        loop=loop_policy,
        log_level=str(settings.log_level).lower(),
        access_log=settings.uvicorn_access_log,
        log_config=None,
    )


if __name__ == "__main__":
    main()

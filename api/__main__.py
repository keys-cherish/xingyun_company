"""Run Mini App API server (with uvloop when available)."""

from __future__ import annotations

import uvicorn

from config import settings


def main() -> None:
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
        log_level="info",
    )


if __name__ == "__main__":
    main()

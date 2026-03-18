"""Bot entrypoint (supports polling and webhook)."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from cache.points_redis_client import close_points_redis
from cache.redis_client import close_redis
from config import settings
from db.engine import init_db
from scheduler.daily_settlement import (
    _create_db_backup,
    _upload_to_webdav,
    set_bot,
    start_scheduler,
    stop_scheduler,
)
from utils.logging_setup import setup_logging

setup_logging("bot")
logger = logging.getLogger(__name__)

# Set by --reload runner child process to skip expensive startup work.
_RELOAD_MODE = os.environ.get("_BOT_RELOAD") == "1"


def _build_fsm_storage() -> BaseStorage:
    """Prefer Redis FSM storage to avoid long-running in-process memory growth."""
    redis_url = (settings.redis_url or "").strip()
    if not redis_url:
        logger.info("FSM storage: memory")
        return MemoryStorage()

    try:
        import redis.asyncio as aioredis
        from aiogram.fsm.storage.redis import RedisStorage

        redis_client = aioredis.from_url(redis_url)
        # Expire stale state/data so abandoned conversations do not accumulate forever.
        storage = RedisStorage(redis=redis_client, state_ttl=24 * 3600, data_ttl=24 * 3600)
        logger.info("FSM storage: redis")
        return storage
    except Exception:
        logger.exception("Failed to initialize Redis FSM storage, falling back to memory")
        return MemoryStorage()


def _register_routers(dp: Dispatcher) -> None:
    from handlers.ad import router as ad_router
    from handlers.admin import router as admin_router
    from handlers.ai_chat import router as ai_chat_router
    from handlers.ai_rd import router as ai_rd_router
    from handlers.battle import router as battle_router
    from handlers.bounty import router as bounty_router
    from handlers.checkin import router as checkin_router
    from handlers.demon_event import router as demon_event_router
    from handlers.company import router as company_router
    from handlers.company_employees import router as company_employees_router
    from handlers.company_ops import router as company_ops_router
    from handlers.cooperation import router as cooperation_router
    from handlers.dividend import router as dividend_router
    from handlers.exchange import router as exchange_router
    from handlers.funds import router as funds_router
    from handlers.product import router as product_router
    from handlers.quest import router as quest_router
    from handlers.realestate import router as realestate_router
    from handlers.redpacket import router as redpacket_router
    from handlers.research import router as research_router
    from handlers.roadshow import router as roadshow_router
    from handlers.roulette import router as roulette_router
    from handlers.shareholder import router as shareholder_router
    from handlers.slot_machine import router as slot_router
    from handlers.start import router as start_router
    from handlers.total_war import router as total_war_router

    dp.include_router(start_router)
    dp.include_router(company_router)
    dp.include_router(company_ops_router)
    dp.include_router(company_employees_router)
    dp.include_router(shareholder_router)
    dp.include_router(research_router)
    dp.include_router(product_router)
    dp.include_router(roadshow_router)
    dp.include_router(cooperation_router)
    dp.include_router(realestate_router)
    dp.include_router(dividend_router)
    dp.include_router(funds_router)
    dp.include_router(ad_router)
    dp.include_router(ai_rd_router)
    dp.include_router(total_war_router)
    dp.include_router(ai_chat_router)
    dp.include_router(admin_router)
    dp.include_router(exchange_router)
    dp.include_router(battle_router)
    dp.include_router(quest_router)
    dp.include_router(slot_router)
    dp.include_router(checkin_router)
    dp.include_router(redpacket_router)
    dp.include_router(bounty_router)
    dp.include_router(demon_event_router)
    dp.include_router(roulette_router)

    # Private chat fallback: non-admin users can only use known command prefixes.
    from aiogram import F, Router
    from aiogram.fsm.context import FSMContext
    from handlers.common import reject_private

    fallback = Router()

    @fallback.message(F.chat.type == "private")
    async def _private_fallback(message, state: FSMContext):
        current_state = await state.get_state()
        if current_state is not None:
            return
        allowed_prefixes = (
            "/cp",
            "/cp_list",
            "/cp_log",
            "/cp_transfer",
            "/cp_member",
            "/cp_invest",
            "/cp_dividend",
            "/cp_exchange",
            "/cp_rename",
            "/cp_start",
            "/cp_create",
            "/cp_help",
            "/cp_battle",
            "/cp_cooperate",
            "/cp_new_product",
            "/cp_dissolve",
            "/cp_rank",
            "/cp_makeup",
            "/cp_give",
            "/cp_welfare",
            "/cp_undo",
            "/cp_quest",
            "/cp_cleanup",
            "/cp_maintain",
            "/cp_compensate",
            "/cp_cancel",
            "/cp_checkin",
            "/cp_redpacket",
            "/cp_slot",
        )
        if message.text and message.text.startswith(allowed_prefixes):
            return
        await reject_private(message)

    dp.include_router(fallback)


def _normalize_run_mode() -> str:
    mode = (settings.run_mode or "polling").strip().lower()
    if mode not in {"polling", "webhook"}:
        logger.warning("Invalid RUN_MODE=%r, fallback to polling", settings.run_mode)
        return "polling"
    return mode


def _normalize_webhook_path(raw_path: str) -> str:
    path = (raw_path or "/tg/webhook").strip()
    if not path:
        path = "/tg/webhook"
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _build_webhook_url(base_url: str, path: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{path}"


def _build_webhook_ssl_context() -> ssl.SSLContext | None:
    certfile = (settings.webhook_ssl_certfile or "").strip()
    keyfile = (settings.webhook_ssl_keyfile or "").strip()
    if not certfile and not keyfile:
        return None
    if not certfile or not keyfile:
        raise RuntimeError(
            "WEBHOOK_SSL_CERTFILE and WEBHOOK_SSL_KEYFILE must both be set for direct HTTPS webhook"
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=certfile, keyfile=keyfile)
    return context


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    logger.info("Bot starting in polling mode...")
    # Polling startup cleanup to avoid update-mode conflicts on Telegram side.
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Polling mode: startup cleanup completed before getUpdates")
    except Exception:
        logger.exception("Polling mode: startup cleanup failed before start_polling")

    await dp.start_polling(
        bot,
        polling_timeout=30,
        backoff_on_timeout=5,
        drop_pending_updates=True,
    )


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    path = _normalize_webhook_path(settings.webhook_path)
    webhook_url = _build_webhook_url(settings.webhook_base_url, path)
    if not webhook_url:
        raise RuntimeError("RUN_MODE=webhook but WEBHOOK_BASE_URL is empty")

    secret_token = (settings.webhook_secret_token or "").strip() or None
    max_connections = max(1, min(100, int(settings.webhook_max_connections)))
    ssl_context = _build_webhook_ssl_context()

    if settings.webhook_set_on_startup:
        await bot.set_webhook(
            url=webhook_url,
            secret_token=secret_token,
            drop_pending_updates=bool(settings.webhook_drop_pending_updates),
            max_connections=max_connections,
            allowed_updates=dp.resolve_used_update_types(),
        )
        logger.info("Webhook configured: %s", webhook_url)
    else:
        logger.info("WEBHOOK_SET_ON_STARTUP=false, skip setWebhook")

    app = web.Application()
    request_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        handle_in_background=True,
        secret_token=secret_token,
    )
    request_handler.register(app, path=path)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner,
        host=settings.webhook_host,
        port=int(settings.webhook_port),
        ssl_context=ssl_context,
    )
    await site.start()

    logger.info(
        "Bot starting in webhook mode: listen=%s:%s path=%s",
        settings.webhook_host,
        settings.webhook_port,
        path,
    )
    if ssl_context is None:
        logger.warning(
            "Webhook server started without local TLS cert. Ensure your platform already terminates HTTPS before forwarding."
        )

    try:
        await asyncio.Event().wait()
    finally:
        try:
            await runner.cleanup()
        except Exception:
            logger.exception("Webhook server shutdown failed")
        if settings.webhook_delete_on_shutdown:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                logger.info("Webhook deleted on shutdown")
            except Exception:
                logger.exception("Failed to delete webhook on shutdown")


async def main() -> None:
    if not settings.bot_token:
        logger.error("BOT_TOKEN is empty. Please configure it in .env")
        return

    loop = asyncio.get_running_loop()

    def _log_asyncio_exception(_loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        message = context.get("message", "Unhandled asyncio exception")
        if exc is not None:
            logger.error(message, exc_info=exc)
        else:
            logger.error(message)

    loop.set_exception_handler(_log_asyncio_exception)

    bot = Bot(
        token=settings.bot_token,
        session=AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=_build_fsm_storage())

    if not _RELOAD_MODE:
        await init_db()
        logger.info("Database initialization complete")
    else:
        logger.info("Reload mode: skip database initialization")

    _register_routers(dp)

    from utils.maintenance import MaintenanceModeMiddleware
    from utils.panel_auth import PanelOwnerMiddleware
    from utils.stream_event import StreamEventMiddleware
    from utils.throttle import ThrottleMiddleware
    from utils.topic_gate import TelegramErrorGuardMiddleware, TopicGateMiddleware
    from utils.callback_dedup import CallbackDedupMiddleware

    # Middleware order matters: error-guard should be the outermost for handler errors.
    dp.message.middleware(TelegramErrorGuardMiddleware())
    dp.callback_query.middleware(TelegramErrorGuardMiddleware())
    dp.message.middleware(TopicGateMiddleware())
    dp.callback_query.middleware(TopicGateMiddleware())
    dp.callback_query.middleware(CallbackDedupMiddleware())
    dp.message.middleware(MaintenanceModeMiddleware())
    dp.callback_query.middleware(MaintenanceModeMiddleware())
    dp.message.middleware(StreamEventMiddleware())
    dp.callback_query.middleware(StreamEventMiddleware())
    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())
    dp.callback_query.outer_middleware(PanelOwnerMiddleware())

    if not _RELOAD_MODE:

        async def _deferred_init() -> None:
            try:
                from services.user_service import sync_all_users_to_shared_points

                total_users, changed_users = await sync_all_users_to_shared_points()
                logger.info("Shared points sync complete: users=%d changed=%d", total_users, changed_users)
            except Exception:
                logger.exception("Shared points sync failed")
            try:
                from handlers.start import BOT_COMMANDS

                await bot.set_my_commands(BOT_COMMANDS)
                logger.info("Bot command list registration complete")
            except Exception:
                logger.exception("Bot command list registration failed")

        asyncio.create_task(_deferred_init())

    set_bot(bot)
    from scheduler.holiday_gift import set_bot as set_holiday_bot
    set_holiday_bot(bot)
    from handlers.demon_event import set_bot as set_demon_bot
    set_demon_bot(bot)
    start_scheduler()

    try:
        mode = _normalize_run_mode()
        if mode == "webhook":
            await _run_webhook(bot, dp)
        else:
            await _run_polling(bot, dp)
    finally:
        # Create an extra shutdown backup for safer recovery.
        if not _RELOAD_MODE:
            try:
                file_path, table_counts = await _create_db_backup()
                total_rows = sum(table_counts.values())
                logger.info("Shutdown backup completed: %s (rows=%d)", file_path, total_rows)
                if await _upload_to_webdav(file_path):
                    logger.info("Shutdown backup uploaded to WebDAV")
                    try:
                        file_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                else:
                    logger.warning("Shutdown backup WebDAV upload failed, keep local file: %s", file_path)
            except Exception:
                logger.exception("Shutdown backup failed")

        try:
            await dp.storage.close()
        except Exception:
            pass

        stop_scheduler()
        await close_redis()
        await close_points_redis()
        await bot.session.close()


def _install_uvloop() -> bool:
    """Install uvloop as the default asyncio event loop policy."""
    if not settings.use_uvloop:
        return False
    try:
        import uvloop

        uvloop.install()
        return True
    except ImportError:
        logger.warning("uvloop is not available on this platform, fallback to default asyncio loop")
        return False
    except Exception:
        logger.warning("uvloop install failed, fallback to default asyncio loop", exc_info=True)
        return False


if __name__ == "__main__":
    import sys

    if "--reload" in sys.argv:
        from watchfiles import run_process

        logger.info("Reload mode started, watching .py file changes...")
        os.environ["_BOT_RELOAD"] = "1"
        run_process(
            ".",
            target=lambda: asyncio.run(main()),
            watch_filter=lambda change, path: path.endswith(".py"),
        )
    else:
        if _install_uvloop():
            logger.info("uvloop enabled")
        asyncio.run(main())

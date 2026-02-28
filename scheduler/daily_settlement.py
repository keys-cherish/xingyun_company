"""Daily settlement scheduler using APScheduler."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from db.engine import async_session
from services.settlement_service import format_daily_report, settle_all

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot_ref = None  # will be set at startup


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


async def _daily_job():
    logger.info("Starting daily settlement...")
    async with async_session() as session:
        async with session.begin():
            reports = await settle_all(session)
    logger.info("Daily settlement completed: %d companies processed", len(reports))

    # Refresh black market deals for the new day
    try:
        from services.shop_service import generate_black_market
        await generate_black_market()
        logger.info("Black market refreshed")
    except Exception:
        logger.exception("Failed to refresh black market")

    # Notify owners
    if _bot_ref:
        for company, report, events in reports:
            try:
                text = format_daily_report(company, report, events)
                from db.engine import async_session as _sess
                async with _sess() as s:
                    from db.models import User
                    owner = await s.get(User, company.owner_id)
                    if owner:
                        await _bot_ref.send_message(owner.tg_id, text)
            except Exception:
                logger.exception("Failed to notify owner of company %s", company.name)


def start_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _daily_job,
        "cron",
        hour=settings.settlement_hour,
        minute=settings.settlement_minute,
        id="daily_settlement",
    )
    _scheduler.start()
    logger.info("Scheduler started: daily settlement at %02d:%02d UTC", settings.settlement_hour, settings.settlement_minute)


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None

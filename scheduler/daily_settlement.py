"""Daily settlement scheduler using APScheduler."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
from decimal import Decimal
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import text

from config import settings
from db.engine import async_session, engine
from services.settlement_service import format_daily_report, settle_all

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot_ref = None  # will be set at startup
_BACKUP_DIR = Path(".")


def set_bot(bot):
    global _bot_ref
    _bot_ref = bot


def _json_safe(value):
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _write_backup_file(path: Path, payload: dict):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def _rotate_old_backups(keep_files: int):
    if keep_files <= 0:
        return
    files = sorted(_BACKUP_DIR.glob("my_company_backup_*.json.gz"))
    overflow = len(files) - keep_files
    if overflow <= 0:
        return
    for old_file in files[:overflow]:
        old_file.unlink(missing_ok=True)


async def _create_db_backup() -> tuple[Path, dict[str, int]]:
    from db.models import Base

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now_utc = dt.datetime.now(dt.UTC)
    file_path = _BACKUP_DIR / f"my_company_backup_{now_utc.strftime('%Y%m%dT%H%M%SZ')}.json.gz"

    table_data: dict[str, list[dict]] = {}
    table_counts: dict[str, int] = {}

    async with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            result = await conn.execute(text(f'SELECT * FROM "{table.name}"'))
            rows = []
            for row in result.mappings().all():
                rows.append({k: _json_safe(v) for k, v in row.items()})
            table_data[table.name] = rows
            table_counts[table.name] = len(rows)

    payload = {
        "project": "my_company",
        "created_at_utc": now_utc.isoformat(),
        "tables": table_data,
    }
    _write_backup_file(file_path, payload)
    _rotate_old_backups(settings.backup_keep_files)
    return file_path, table_counts


async def _notify_backup_status(text_msg: str):
    if not _bot_ref or not settings.backup_notify_super_admin:
        return
    admin_ids = settings.super_admin_tg_id_set
    if not admin_ids:
        return
    for admin_tg_id in admin_ids:
        try:
            await _bot_ref.send_message(admin_tg_id, text_msg)
        except Exception:
            logger.exception("Failed to notify super admin %s for backup", admin_tg_id)


async def _backup_job():
    if not settings.backup_enabled:
        return

    try:
        file_path, table_counts = await _create_db_backup()
        total_rows = sum(table_counts.values())
        summary = ", ".join(f"{name}:{count}" for name, count in sorted(table_counts.items()))
        logger.info("DB backup completed: %s rows=%d", file_path, total_rows)
        await _notify_backup_status(
            "🛡 my_company 自动备份完成\n"
            f"⏰ UTC时间: {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📦 文件: {file_path}\n"
            f"🧾 总行数: {total_rows}\n"
            f"📚 分表: {summary}",
        )
    except Exception as exc:
        logger.exception("DB backup failed")
        await _notify_backup_status(
            "❌ my_company 自动备份失败\n"
            f"⏰ UTC时间: {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"原因: {exc}",
        )


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
    if settings.backup_enabled and settings.backup_interval_minutes > 0:
        _scheduler.add_job(
            _backup_job,
            "interval",
            minutes=settings.backup_interval_minutes,
            id="db_backup",
        )
    _scheduler.start()
    logger.info("Scheduler started: daily settlement at %02d:%02d UTC", settings.settlement_hour, settings.settlement_minute)
    if settings.backup_enabled and settings.backup_interval_minutes > 0:
        logger.info(
            "Scheduler started: DB backup every %d minutes (keep %d files)",
            settings.backup_interval_minutes,
            settings.backup_keep_files,
        )


def stop_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None

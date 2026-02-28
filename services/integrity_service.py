"""Data integrity checker – auto-fixes illegal states.

Covers: products (employee over-allocation), shareholders (share overflow),
research (orphaned), real estate (orphaned), cooperations (expired/orphaned).
"""

from __future__ import annotations

import logging

from sqlalchemy import select, func as sqlfunc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Company, Product

logger = logging.getLogger(__name__)


async def cleanup_illegal_products(session: AsyncSession) -> list[str]:
    """Remove products whose assigned_employees exceed company's total employees.

    Keeps products with highest daily_income, removes lowest income first.
    """
    msgs = []
    result = await session.execute(select(Company))
    companies = list(result.scalars().all())

    for company in companies:
        # Sort by daily_income ASC so we remove lowest-income products first
        products = list((await session.execute(
            select(Product)
            .where(Product.company_id == company.id)
            .order_by(Product.daily_income.asc())
        )).scalars().all())

        total_assigned = sum(p.assigned_employees for p in products)
        if total_assigned <= company.employee_count:
            continue

        # Remove lowest-income products until assigned <= employee count
        removed = []
        for product in products:  # lowest income first
            if total_assigned <= company.employee_count:
                break
            total_assigned -= product.assigned_employees
            removed.append(product.name)
            await session.delete(product)

        if removed:
            from services.company_service import update_daily_revenue
            await update_daily_revenue(session, company.id)
            msg = f"⚠️ 「{company.name}」员工不足，自动下架(保留高收入): {', '.join(removed)}"
            msgs.append(msg)
            logger.warning(msg)

    if msgs:
        await session.flush()
    return msgs


async def cleanup_illegal_shareholders(session: AsyncSession) -> list[str]:
    """Fix shareholder data: total shares per company must not exceed 100%."""
    from db.models import Shareholder
    msgs = []

    result = await session.execute(select(Company))
    companies = list(result.scalars().all())

    for company in companies:
        shareholders = list((await session.execute(
            select(Shareholder)
            .where(Shareholder.company_id == company.id)
            .order_by(Shareholder.shares.desc())
        )).scalars().all())

        total_shares = sum(s.shares for s in shareholders)
        if total_shares <= 100.0:
            continue

        # Normalize shares proportionally
        for sh in shareholders:
            sh.shares = round(sh.shares / total_shares * 100.0, 2)

        msg = f"⚠️ 「{company.name}」股份总和 {total_shares:.1f}% > 100%，已按比例修正"
        msgs.append(msg)
        logger.warning(msg)

    if msgs:
        await session.flush()
    return msgs


async def cleanup_orphaned_research(session: AsyncSession) -> list[str]:
    """Remove research records that reference non-existent companies."""
    from db.models import ResearchProgress
    msgs = []

    orphaned = (await session.execute(
        select(ResearchProgress).where(
            ~ResearchProgress.company_id.in_(select(Company.id))
        )
    )).scalars().all()

    if orphaned:
        for r in orphaned:
            await session.delete(r)
        msg = f"⚠️ 清理了 {len(orphaned)} 条孤立研发记录"
        msgs.append(msg)
        logger.warning(msg)
        await session.flush()

    return msgs


async def cleanup_orphaned_realestate(session: AsyncSession) -> list[str]:
    """Remove real estate that references non-existent companies."""
    from db.models import RealEstate
    msgs = []

    orphaned = (await session.execute(
        select(RealEstate).where(
            ~RealEstate.company_id.in_(select(Company.id))
        )
    )).scalars().all()

    if orphaned:
        for r in orphaned:
            await session.delete(r)
        msg = f"⚠️ 清理了 {len(orphaned)} 条孤立地产记录"
        msgs.append(msg)
        logger.warning(msg)
        await session.flush()

    return msgs


async def cleanup_expired_cooperations(session: AsyncSession) -> list[str]:
    """Remove cooperations that reference non-existent companies."""
    from services.cooperation_service import Cooperation
    msgs = []

    orphaned = (await session.execute(
        select(Cooperation).where(
            (~Cooperation.company_a_id.in_(select(Company.id)))
            | (~Cooperation.company_b_id.in_(select(Company.id)))
        )
    )).scalars().all()

    if orphaned:
        for c in orphaned:
            await session.delete(c)
        msg = f"⚠️ 清理了 {len(orphaned)} 条孤立合作记录"
        msgs.append(msg)
        logger.warning(msg)
        await session.flush()

    return msgs


async def cleanup_negative_funds(session: AsyncSession) -> list[str]:
    """Fix companies with negative funds."""
    from sqlalchemy import update
    msgs = []

    result = await session.execute(
        select(Company).where(Company.total_funds < 0)
    )
    negative_companies = list(result.scalars().all())

    for company in negative_companies:
        company.total_funds = 0
        msg = f"⚠️ 「{company.name}」资金为负数，已修正为0"
        msgs.append(msg)
        logger.warning(msg)

    if msgs:
        await session.flush()
    return msgs


async def run_all_checks(session: AsyncSession) -> list[str]:
    """Run all data integrity checks."""
    msgs = []
    msgs.extend(await cleanup_illegal_products(session))
    msgs.extend(await cleanup_illegal_shareholders(session))
    msgs.extend(await cleanup_orphaned_research(session))
    msgs.extend(await cleanup_orphaned_realestate(session))
    msgs.extend(await cleanup_expired_cooperations(session))
    msgs.extend(await cleanup_negative_funds(session))
    return msgs

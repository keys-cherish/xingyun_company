"""Data integrity checker – auto-fixes illegal states."""

from __future__ import annotations

import logging

from sqlalchemy import select, func as sqlfunc, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Company, Product

logger = logging.getLogger(__name__)


async def cleanup_illegal_products(session: AsyncSession) -> list[str]:
    """Remove products whose assigned_employees exceed company's total employees.

    When a company has 10 employees but products claim 15 assigned,
    remove products (newest first) until assigned <= company employees.
    """
    msgs = []
    result = await session.execute(select(Company))
    companies = list(result.scalars().all())

    for company in companies:
        products = (await session.execute(
            select(Product)
            .where(Product.company_id == company.id)
            .order_by(Product.created_at.desc())
        )).scalars().all()

        total_assigned = sum(p.assigned_employees for p in products)
        if total_assigned <= company.employee_count:
            continue

        # Need to remove products until assigned <= employee count
        removed = []
        for product in products:  # newest first
            if total_assigned <= company.employee_count:
                break
            total_assigned -= product.assigned_employees
            removed.append(product.name)
            await session.delete(product)

        if removed:
            msg = f"⚠️ 「{company.name}」员工不足，自动下架: {', '.join(removed)}"
            msgs.append(msg)
            logger.warning(msg)

    if msgs:
        await session.flush()
    return msgs


async def run_all_checks(session: AsyncSession) -> list[str]:
    """Run all data integrity checks."""
    msgs = []
    msgs.extend(await cleanup_illegal_products(session))
    return msgs

"""Dividend distribution system."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, Shareholder
from services.shareholder_service import get_shareholders
from services.user_service import add_reputation, add_traffic, add_points


async def distribute_dividends(
    session: AsyncSession,
    company: Company,
    profit: int,
) -> list[tuple[int, int]]:
    """Distribute profit to shareholders. Returns list of (user_id, amount)."""
    if profit <= 0:
        return []

    dividend_pool = int(profit * settings.dividend_pct)
    if dividend_pool <= 0:
        return []

    shareholders = await get_shareholders(session, company.id)
    distributions: list[tuple[int, int]] = []

    for sh in shareholders:
        share_amount = int(dividend_pool * sh.shares / 100)
        if share_amount > 0:
            await add_traffic(session, sh.user_id, share_amount)
            distributions.append((sh.user_id, share_amount))

            # Reputation gain from receiving dividends
            rep = settings.reputation_per_dividend
            await add_reputation(session, sh.user_id, rep)

            # Points for receiving dividends
            await add_points(sh.user_id, 2)

    return distributions

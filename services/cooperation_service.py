"""Cooperation between companies."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, Cooperation
from services.user_service import add_reputation, add_points

DEFAULT_COOP_DAYS = 7
DEFAULT_BONUS_MULTIPLIER = 0.10  # +10% revenue


async def get_active_cooperations(session: AsyncSession, company_id: int) -> list[Cooperation]:
    now = dt.datetime.now(dt.timezone.utc)
    result = await session.execute(
        select(Cooperation).where(
            or_(
                Cooperation.company_a_id == company_id,
                Cooperation.company_b_id == company_id,
            ),
            Cooperation.expires_at > now,
        )
    )
    return list(result.scalars().all())


async def get_cooperation_bonus(session: AsyncSession, company_id: int) -> float:
    """Return total cooperation bonus multiplier for a company."""
    coops = await get_active_cooperations(session, company_id)
    if not coops:
        return 0.0
    # Non-stackable buff: take the highest single multiplier
    return max(c.bonus_multiplier for c in coops)


async def create_cooperation(
    session: AsyncSession,
    company_a_id: int,
    company_b_id: int,
    days: int = DEFAULT_COOP_DAYS,
) -> tuple[bool, str]:
    """Establish cooperation between two companies."""
    if company_a_id == company_b_id:
        return False, "不能与自己合作"

    # Check both exist
    ca = await session.get(Company, company_a_id)
    cb = await session.get(Company, company_b_id)
    if not ca or not cb:
        return False, "公司不存在"

    # Check not already cooperating
    existing = await get_active_cooperations(session, company_a_id)
    for c in existing:
        partner = c.company_b_id if c.company_a_id == company_a_id else c.company_a_id
        if partner == company_b_id:
            return False, "已有合作关系"

    now = dt.datetime.now(dt.timezone.utc)
    coop = Cooperation(
        company_a_id=company_a_id,
        company_b_id=company_b_id,
        bonus_multiplier=DEFAULT_BONUS_MULTIPLIER,
        expires_at=now + dt.timedelta(days=days),
    )
    session.add(coop)
    await session.flush()

    # Grant reputation to both owners
    rep = settings.reputation_per_cooperation
    await add_reputation(session, ca.owner_id, rep)
    await add_reputation(session, cb.owner_id, rep)

    # Points
    await add_points(ca.owner_id, 8, session=session)
    await add_points(cb.owner_id, 8, session=session)

    return True, f"「{ca.name}」与「{cb.name}」建立合作! 营收加成+{DEFAULT_BONUS_MULTIPLIER*100:.0f}%，有效期{days}天"

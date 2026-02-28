"""Cooperation between companies – daily expiry, stackable up to cap."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, Cooperation
from services.user_service import add_reputation, add_points

DEFAULT_BONUS_MULTIPLIER = 0.10  # +10% per cooperation
COOP_CAP_NORMAL = 0.50          # 50% max for normal companies
COOP_CAP_MAX_LEVEL = 1.00       # 100% max for max-level companies


def _utc_now_naive() -> dt.datetime:
    """Return current UTC time as naive datetime for TIMESTAMP WITHOUT TIME ZONE."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _next_settlement_time() -> dt.datetime:
    """Return the next 00:00 UTC as expiry time (daily reset)."""
    now = _utc_now_naive()
    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow


def _get_coop_cap(company_level: int) -> float:
    """Return cooperation bonus cap based on company level."""
    from services.company_service import get_max_level
    if company_level >= get_max_level():
        return COOP_CAP_MAX_LEVEL
    return COOP_CAP_NORMAL


async def get_active_cooperations(session: AsyncSession, company_id: int) -> list[Cooperation]:
    now = _utc_now_naive()
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
    """Return total cooperation bonus multiplier for a company (stackable, capped)."""
    coops = await get_active_cooperations(session, company_id)
    if not coops:
        return 0.0
    company = await session.get(Company, company_id)
    cap = _get_coop_cap(company.level) if company else COOP_CAP_NORMAL
    total = sum(c.bonus_multiplier for c in coops)
    return min(total, cap)


async def create_cooperation(
    session: AsyncSession,
    company_a_id: int,
    company_b_id: int,
) -> tuple[bool, str]:
    """Establish cooperation between two companies (expires at next settlement)."""
    if company_a_id == company_b_id:
        return False, "不能与自己合作"

    ca = await session.get(Company, company_a_id)
    cb = await session.get(Company, company_b_id)
    if not ca or not cb:
        return False, "公司不存在"

    # Check not already cooperating
    existing = await get_active_cooperations(session, company_a_id)
    for c in existing:
        partner = c.company_b_id if c.company_a_id == company_a_id else c.company_a_id
        if partner == company_b_id:
            return False, f"已与「{cb.name}」合作中"

    # Check cap
    cap = _get_coop_cap(ca.level)
    current_total = sum(c.bonus_multiplier for c in existing)
    if current_total >= cap:
        cap_pct = int(cap * 100)
        return False, f"合作加成已达上限 {cap_pct}%"

    expires_at = _next_settlement_time()
    coop = Cooperation(
        company_a_id=company_a_id,
        company_b_id=company_b_id,
        bonus_multiplier=DEFAULT_BONUS_MULTIPLIER,
        expires_at=expires_at,
    )
    session.add(coop)
    await session.flush()

    # Grant reputation and points to both owners
    rep = settings.reputation_per_cooperation
    await add_reputation(session, ca.owner_id, rep)
    await add_reputation(session, cb.owner_id, rep)
    await add_points(ca.owner_id, 8, session=session)
    await add_points(cb.owner_id, 8, session=session)

    return True, f"「{ca.name}」与「{cb.name}」建立合作! 营收+{DEFAULT_BONUS_MULTIPLIER*100:.0f}%（今日有效）"


async def cooperate_all(
    session: AsyncSession,
    my_company_id: int,
) -> tuple[int, int, list[str]]:
    """Cooperate with all other companies. Returns (success_count, skip_count, messages)."""
    result = await session.execute(select(Company).where(Company.id != my_company_id))
    all_companies = list(result.scalars().all())

    my_company = await session.get(Company, my_company_id)
    if not my_company:
        return 0, 0, ["公司不存在"]

    cap = _get_coop_cap(my_company.level)
    existing = await get_active_cooperations(session, my_company_id)
    current_total = sum(c.bonus_multiplier for c in existing)
    existing_partners = set()
    for c in existing:
        partner = c.company_b_id if c.company_a_id == my_company_id else c.company_a_id
        existing_partners.add(partner)

    success = 0
    skip = 0
    msgs = []

    for target in all_companies:
        if current_total >= cap:
            skip += len(all_companies) - success - skip
            msgs.append(f"合作加成已达上限 {int(cap*100)}%，停止")
            break
        if target.id in existing_partners:
            skip += 1
            continue

        expires_at = _next_settlement_time()
        coop = Cooperation(
            company_a_id=my_company_id,
            company_b_id=target.id,
            bonus_multiplier=DEFAULT_BONUS_MULTIPLIER,
            expires_at=expires_at,
        )
        session.add(coop)
        current_total += DEFAULT_BONUS_MULTIPLIER
        success += 1

        # Reputation & points
        rep = settings.reputation_per_cooperation
        await add_reputation(session, my_company.owner_id, rep)
        await add_reputation(session, target.owner_id, rep)
        await add_points(my_company.owner_id, 8, session=session)
        await add_points(target.owner_id, 8, session=session)

    await session.flush()
    return success, skip, msgs


async def cooperate_with(
    session: AsyncSession,
    my_company_id: int,
    target_company_id: int,
) -> tuple[bool, str]:
    """Cooperate with a specific company by ID."""
    return await create_cooperation(session, my_company_id, target_company_id)

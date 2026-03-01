"""Cooperation between companies – daily expiry, stackable up to cap."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Company, Cooperation
from services.user_service import add_reputation, add_points
from utils.timezone import BJ_TZ

DEFAULT_BONUS_MULTIPLIER = 0.05  # +5% per cooperation
COOP_REPUTATION_GAIN = 30


def _utc_now_naive() -> dt.datetime:
    """Return current UTC time as naive datetime for TIMESTAMP WITHOUT TIME ZONE."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _next_settlement_time() -> dt.datetime:
    """Return the next 00:00 Beijing as naive UTC for DB storage."""
    now_bj = dt.datetime.now(BJ_TZ)
    next_bj = (now_bj + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return next_bj.astimezone(dt.UTC).replace(tzinfo=None)


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
    """Return total cooperation bonus multiplier for a company."""
    coops = await get_active_cooperations(session, company_id)
    if not coops:
        return 0.0
    return sum(c.bonus_multiplier for c in coops)


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

    # Daily limit: each company can only cooperate with one company per day.
    a_active = await get_active_cooperations(session, company_a_id)
    if a_active:
        partner_id = a_active[0].company_b_id if a_active[0].company_a_id == company_a_id else a_active[0].company_a_id
        partner = await session.get(Company, partner_id)
        partner_name = partner.name if partner else "未知"
        return False, f"你今天已与「{partner_name}」合作，每天仅可合作一家"
    b_active = await get_active_cooperations(session, company_b_id)
    if b_active:
        return False, f"❌ 对方公司今天已与其他公司合作"

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
    await add_reputation(session, ca.owner_id, COOP_REPUTATION_GAIN)
    await add_reputation(session, cb.owner_id, COOP_REPUTATION_GAIN)
    await add_points(ca.owner_id, 8, session=session)
    await add_points(cb.owner_id, 8, session=session)

    # Quest progress
    from services.quest_service import update_quest_progress
    await update_quest_progress(session, ca.owner_id, "cooperation_count", increment=1)
    await update_quest_progress(session, cb.owner_id, "cooperation_count", increment=1)

    return True, (
        f"「{ca.name}」与「{cb.name}」建立合作! 营收+{DEFAULT_BONUS_MULTIPLIER*100:.0f}%（今日有效）\n"
        f"双方各 +{COOP_REPUTATION_GAIN} 声望（今日首次合作）"
    )


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

    existing = await get_active_cooperations(session, my_company_id)
    if existing:
        partner_id = existing[0].company_b_id if existing[0].company_a_id == my_company_id else existing[0].company_a_id
        partner = await session.get(Company, partner_id)
        partner_name = partner.name if partner else "未知"
        return 0, len(all_companies), [f"今天已与「{partner_name}」合作，每天仅可合作一家"]

    success = 0
    skip = 0
    msgs = []

    for target in all_companies:
        target_active = await get_active_cooperations(session, target.id)
        if target_active:
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
        success += 1

        # Reputation & points
        await add_reputation(session, my_company.owner_id, COOP_REPUTATION_GAIN)
        await add_reputation(session, target.owner_id, COOP_REPUTATION_GAIN)
        await add_points(my_company.owner_id, 8, session=session)
        await add_points(target.owner_id, 8, session=session)

        # Quest progress
        from services.quest_service import update_quest_progress
        await update_quest_progress(session, my_company.owner_id, "cooperation_count", increment=1)
        await update_quest_progress(session, target.owner_id, "cooperation_count", increment=1)
        break

    await session.flush()
    return success, skip, msgs


async def cooperate_with(
    session: AsyncSession,
    my_company_id: int,
    target_company_id: int,
) -> tuple[bool, str]:
    """Cooperate with a specific company by ID."""
    return await create_cooperation(session, my_company_id, target_company_id)

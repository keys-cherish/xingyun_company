"""Real estate purchase and management."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import RealEstate
from services.company_service import add_funds
from services.user_service import add_points

_buildings_data: dict | None = None


def _load_buildings() -> dict:
    global _buildings_data
    if _buildings_data is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "buildings.json"
        with open(path, encoding="utf-8") as f:
            _buildings_data = json.load(f)
    return _buildings_data


def get_building_list() -> list[dict]:
    blds = _load_buildings()
    return [{"key": k, **v} for k, v in blds.items()]


async def get_company_estates(session: AsyncSession, company_id: int) -> list[RealEstate]:
    result = await session.execute(
        select(RealEstate).where(RealEstate.company_id == company_id)
    )
    return list(result.scalars().all())


async def get_total_estate_income(session: AsyncSession, company_id: int) -> int:
    estates = await get_company_estates(session, company_id)
    return sum(e.daily_dividend for e in estates)


async def purchase_building(
    session: AsyncSession,
    company_id: int,
    owner_tg_id: int,
    building_key: str,
) -> tuple[bool, str]:
    """Purchase a building for a company using company funds."""
    buildings = _load_buildings()
    if building_key not in buildings:
        return False, "无效的地产类型"

    bld = buildings[building_key]
    price = bld["purchase_price"]

    ok = await add_funds(session, company_id, -price)
    if not ok:
        return False, f"公司资金不足，需要{price}流量"

    estate = RealEstate(
        company_id=company_id,
        building_type=building_key,
        daily_dividend=bld["daily_dividend"],
        purchase_price=price,
    )
    session.add(estate)
    await session.flush()

    await add_points(owner_tg_id, 15)

    return True, f"成功购买「{bld['name']}」! 每日收益: {bld['daily_dividend']}流量"

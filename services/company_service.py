"""Company creation and management."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, Product, Shareholder, User
from services.user_service import add_traffic

_company_types: dict | None = None


def load_company_types() -> dict:
    global _company_types
    if _company_types is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "company_types.json"
        with open(path, encoding="utf-8") as f:
            _company_types = json.load(f)
    return _company_types


def get_company_type_info(company_type: str) -> dict | None:
    types = load_company_types()
    return types.get(company_type)


async def create_company(
    session: AsyncSession,
    owner: User,
    name: str,
    company_type: str = "tech",
) -> tuple[Company | None, str]:
    """Create a company. Deducts creation cost from owner's traffic."""
    types = load_company_types()
    if company_type not in types:
        return None, "无效的公司类型"

    # Check duplicate name
    exists = await session.execute(select(Company).where(Company.name == name))
    if exists.scalar_one_or_none():
        return None, "公司名称已存在"

    # Deduct traffic
    ok = await add_traffic(session, owner.id, -settings.company_creation_cost)
    if not ok:
        return None, f"流量不足，创建公司需要{settings.company_creation_cost}流量"

    type_info = types[company_type]
    company = Company(
        name=name,
        company_type=company_type,
        owner_id=owner.id,
        total_funds=settings.company_creation_cost,
    )
    session.add(company)
    await session.flush()

    # Owner gets 100% shares
    shareholder = Shareholder(
        company_id=company.id,
        user_id=owner.id,
        shares=100.0,
        invested_amount=settings.company_creation_cost,
    )
    session.add(shareholder)
    await session.flush()
    return company, f"{type_info['emoji']} {type_info['name']}「{name}」创建成功!"


async def get_company_by_id(session: AsyncSession, company_id: int) -> Company | None:
    return await session.get(Company, company_id)


async def get_companies_by_owner(session: AsyncSession, owner_id: int) -> list[Company]:
    result = await session.execute(select(Company).where(Company.owner_id == owner_id))
    return list(result.scalars().all())


async def get_company_valuation(session: AsyncSession, company: Company) -> int:
    """Valuation = total_funds * coeff + daily_revenue * 30."""
    return int(
        company.total_funds * settings.valuation_fund_coeff
        + company.daily_revenue * settings.valuation_income_days
    )


async def update_daily_revenue(session: AsyncSession, company_id: int) -> int:
    """Recalculate daily revenue from products and return it."""
    result = await session.execute(select(Product).where(Product.company_id == company_id))
    products = result.scalars().all()
    total = sum(p.daily_income for p in products)
    await session.execute(
        update(Company).where(Company.id == company_id).values(daily_revenue=total)
    )
    return total


async def add_funds(session: AsyncSession, company_id: int, amount: int) -> bool:
    """Atomically add/subtract funds with optimistic locking."""
    company = await session.get(Company, company_id)
    if company is None:
        return False
    if amount < 0 and company.total_funds + amount < 0:
        return False
    old_ver = company.version
    result = await session.execute(
        update(Company)
        .where(Company.id == company_id, Company.version == old_ver)
        .values(total_funds=Company.total_funds + amount, version=Company.version + 1)
    )
    if result.rowcount == 0:
        return False
    company.total_funds += amount
    company.version += 1
    return True

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

    # 先保存owner_id，因为add_traffic会expire owner对象
    owner_id = owner.id

    # Deduct traffic
    ok = await add_traffic(session, owner_id, -settings.company_creation_cost)
    if not ok:
        from utils.formatters import fmt_traffic
        return None, f"金币不足，创建公司需要 {fmt_traffic(settings.company_creation_cost)}"

    type_info = types[company_type]
    company = Company(
        name=name,
        company_type=company_type,
        owner_id=owner_id,
        total_funds=settings.company_creation_cost,
    )
    session.add(company)
    await session.flush()

    # Owner gets 100% shares
    shareholder = Shareholder(
        company_id=company.id,
        user_id=owner_id,
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
    # 立即刷新对象，避免惰性加载导致MissingGreenlet
    await session.refresh(company)
    return True


# ---------- Company levels ----------

_company_levels: dict | None = None


def load_company_levels() -> dict:
    global _company_levels
    if _company_levels is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "company_levels.json"
        with open(path, encoding="utf-8") as f:
            _company_levels = json.load(f)
    return _company_levels


def get_level_info(level: int) -> dict | None:
    data = load_company_levels()
    return data["levels"].get(str(level))


def get_max_level() -> int:
    data = load_company_levels()
    return data.get("max_level", 10)


def get_level_revenue_bonus(level: int) -> int:
    """Get cumulative daily revenue bonus from all levels up to and including current level."""
    data = load_company_levels()
    total = 0
    for lv in range(1, level + 1):
        info = data["levels"].get(str(lv))
        if info:
            total += info.get("daily_revenue_bonus", 0)
    return total


def get_level_employee_bonus(level: int) -> int:
    """Get cumulative employee limit bonus from all levels."""
    data = load_company_levels()
    total = 0
    for lv in range(1, level + 1):
        info = data["levels"].get(str(lv))
        if info:
            total += info.get("employee_limit_bonus", 0)
    return total


async def upgrade_company(
    session: AsyncSession,
    company_id: int,
) -> tuple[bool, str]:
    """Upgrade company to next level. Costs company funds."""
    from utils.formatters import fmt_traffic

    company = await session.get(Company, company_id)
    if company is None:
        return False, "公司不存在"

    max_level = get_max_level()
    if company.level >= max_level:
        return False, f"已达最高等级 Lv.{max_level}"

    next_level = company.level + 1
    next_info = get_level_info(next_level)
    if not next_info:
        return False, "等级数据异常"

    cost = next_info["upgrade_cost"]
    ok = await add_funds(session, company_id, -cost)
    if not ok:
        return False, f"公司资金不足，升级到 Lv.{next_level} 需要 {fmt_traffic(cost)}"

    company.level = next_level
    await session.flush()
    await session.refresh(company)

    return True, (
        f"升级成功! {company.name} → Lv.{next_level} 「{next_info['name']}」\n"
        f"{next_info['description']}\n"
        f"永久日营收加成: +{fmt_traffic(next_info['daily_revenue_bonus'])}\n"
        f"员工上限: +{next_info['employee_limit_bonus']}"
    )

"""产品创建、迭代和管理。

修复要点：
- 产品迭代有每日CD（每个产品每天只能迭代1次）
- 产品收入有上限
- 产品名称重复检测
- 版本上限
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import select, func as sqlfunc
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from config import settings
from db.models import Company, Product, User
from services.company_service import get_effective_employee_count_for_progress
from services.research_service import check_and_complete_research, get_completed_techs
from services.user_service import add_points
from utils.formatters import fmt_traffic
from utils.validators import validate_name

_products_data: dict | None = None

# 产品收入上限和版本上限
MAX_PRODUCT_DAILY_INCOME = 500_000
MAX_PRODUCT_VERSION = 50
MAX_DAILY_PRODUCT_CREATE = 3

# Progressive gate: higher stage requires stronger company capability.
PRODUCT_CREATE_COST_GROWTH = 0.30
PRODUCT_CREATE_REPUTATION_STEP = 6
PRODUCT_CREATE_EMPLOYEE_STEP = 1
PRODUCT_UPGRADE_REPUTATION_STEP = 12
PRODUCT_UPGRADE_EMPLOYEE_STEP = 2


def _load_products() -> dict:
    global _products_data
    if _products_data is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "products.json"
        with open(path, encoding="utf-8") as f:
            _products_data = json.load(f)
    return _products_data


async def get_available_product_templates(session: AsyncSession, company_id: int) -> list[dict]:
    """返回公司可创建的产品模板（基于已完成科研）。"""
    await check_and_complete_research(session, company_id)
    completed = set(await get_completed_techs(session, company_id))
    templates = _load_products()
    available = []
    for key, info in templates.items():
        if info["tech_id"] in completed:
            available.append({"product_key": key, **info})
    return available


async def get_company_products(session: AsyncSession, company_id: int) -> list[Product]:
    result = await session.execute(
        select(Product).where(Product.company_id == company_id).order_by(Product.daily_income.desc())
    )
    return list(result.scalars().all())


async def create_product(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
    product_key: str,
    custom_name: str = "",
) -> tuple[Product | None, str]:
    """从模板创建产品。消耗流量。"""
    templates = _load_products()
    if product_key not in templates:
        return None, "无效的产品模板"

    company = await session.get(Company, company_id)
    if company is None:
        return None, "公司不存在"
    if company.owner_id != owner_user_id:
        return None, "只有公司老板才能创建产品"

    tmpl = templates[product_key]
    owner = await session.get(User, owner_user_id)
    if owner is None:
        return None, "用户不存在"

    # 检查前置科研
    await check_and_complete_research(session, company_id)
    completed = set(await get_completed_techs(session, company_id))
    if tmpl["tech_id"] not in completed:
        return None, "需要先完成对应科研"

    # 产品名称
    name = custom_name.strip() if custom_name.strip() else tmpl["name"]
    name_err = validate_name(name, min_len=1, max_len=32)
    if name_err:
        return None, name_err

    # 重名检测（同公司内）
    existing = await session.execute(
        select(Product).where(Product.company_id == company_id, Product.name == name)
    )
    if existing.scalar_one_or_none():
        return None, f"已存在同名产品「{name}」"

    # 每日创建上限
    today_start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    today_count = (await session.execute(
        select(sqlfunc.count()).select_from(Product).where(
            Product.company_id == company_id,
            Product.created_at >= today_start,
        )
    )).scalar() or 0
    if today_count >= MAX_DAILY_PRODUCT_CREATE:
        return None, f"每日最多创建{MAX_DAILY_PRODUCT_CREATE}个产品"

    # Progressive requirements based on how many products company already owns.
    existing_count = (await session.execute(
        select(sqlfunc.count()).select_from(Product).where(Product.company_id == company_id)
    )).scalar() or 0

    required_employees = max(1, 1 + existing_count * PRODUCT_CREATE_EMPLOYEE_STEP)
    effective_employees = get_effective_employee_count_for_progress(company.employee_count)
    if effective_employees < required_employees:
        return None, (
            f"\u5458\u5de5\u4e0d\u8db3\uff0c\u521b\u5efa\u8be5\u4ea7\u54c1\u9700\u8981\u81f3\u5c11 {required_employees} \u4eba\uff0c"
            f"\u5f53\u524d\u6709\u6548\u5458\u5de5 {effective_employees} \u4eba\uff08\u603b\u5458\u5de5 {company.employee_count}\uff09"
        )

    required_reputation = max(0, existing_count * PRODUCT_CREATE_REPUTATION_STEP)
    if owner.reputation < required_reputation:
        return None, (
            f"声望不足，创建该产品需要声望 {required_reputation}，"
            f"当前仅 {owner.reputation}"
        )

    dynamic_create_cost = max(
        settings.product_create_cost,
        int(settings.product_create_cost * (1 + existing_count * PRODUCT_CREATE_COST_GROWTH)),
    )

    # 扣除费用（从公司资金）
    from services.company_service import add_funds
    ok = await add_funds(session, company_id, -dynamic_create_cost)
    if not ok:
        return None, f"公司资金不足，需要 {fmt_traffic(dynamic_create_cost)}"

    product = Product(
        company_id=company_id,
        name=name,
        tech_id=tmpl["tech_id"],
        daily_income=tmpl["base_daily_income"],
        quality=tmpl["base_quality"],
    )
    session.add(product)
    await session.flush()

    await add_points(owner_user_id, 10, session=session)

    # Quest progress
    from services.quest_service import update_quest_progress
    await update_quest_progress(session, owner_user_id, "product_count", increment=1)

    return product, (
        f"产品「{name}」打造成功! 日收入: {fmt_traffic(tmpl['base_daily_income'])} "
        f"(研发投入: {fmt_traffic(dynamic_create_cost)})"
    )


async def upgrade_product(
    session: AsyncSession,
    product_id: int,
    owner_user_id: int,
) -> tuple[bool, str]:
    """升级产品：+版本 +收入 +品质。每个产品每天只能迭代1次。"""
    product = await session.get(Product, product_id)
    if product is None:
        return False, "产品不存在"

    company = await session.get(Company, product.company_id)
    if company is None:
        return False, "公司不存在"
    if company.owner_id != owner_user_id:
        return False, "只有公司老板才能升级产品"
    owner = await session.get(User, owner_user_id)
    if owner is None:
        return False, "用户不存在"

    # 版本上限
    if product.version >= MAX_PRODUCT_VERSION:
        return False, f"产品已达最高版本(v{MAX_PRODUCT_VERSION})"

    # 收入上限
    if product.daily_income >= MAX_PRODUCT_DAILY_INCOME:
        return False, f"产品日收入已达上限({MAX_PRODUCT_DAILY_INCOME})"

    # 每日CD检查（每个产品每天只能迭代1次）
    r = await get_redis()
    cd_key = f"product_upgrade_cd:{product_id}"
    cd_ttl = await r.ttl(cd_key)
    if cd_ttl > 0:
        hours = cd_ttl // 3600
        minutes = (cd_ttl % 3600) // 60
        return False, f"产品迭代冷却中，剩余{hours}时{minutes}分"

    target_version = product.version + 1
    required_employees = max(1, target_version * PRODUCT_UPGRADE_EMPLOYEE_STEP)
    effective_employees = get_effective_employee_count_for_progress(company.employee_count)
    if effective_employees < required_employees:
        return False, (
            f"\u5458\u5de5\u4e0d\u8db3\uff0c\u5347\u7ea7\u5230 v{target_version} \u9700\u8981\u81f3\u5c11 {required_employees} \u4eba\uff0c"
            f"\u5f53\u524d\u6709\u6548\u5458\u5de5 {effective_employees} \u4eba\uff08\u603b\u5458\u5de5 {company.employee_count}\uff09"
        )

    required_reputation = max(0, (target_version - 1) * PRODUCT_UPGRADE_REPUTATION_STEP)
    if owner.reputation < required_reputation:
        return False, (
            f"声望不足，升级到 v{target_version} 需要声望 {required_reputation}，"
            f"当前仅 {owner.reputation}"
        )

    cost = int(settings.product_upgrade_cost_base * (1.3 ** (product.version - 1)))
    from services.company_service import add_funds
    ok = await add_funds(session, product.company_id, -cost)
    if not ok:
        return False, f"公司资金不足，升级需要 {fmt_traffic(cost)}"

    # 迭代收入增幅随版本递减（防止无限刷）
    diminish = max(0.05, settings.product_upgrade_income_pct - (product.version - 1) * 0.01)
    income_boost = max(1, int(product.daily_income * diminish))
    new_income = min(product.daily_income + income_boost, MAX_PRODUCT_DAILY_INCOME)
    actual_boost = new_income - product.daily_income

    product.version += 1
    product.daily_income = new_income
    product.quality = min(product.quality + 3, 100)
    await session.flush()

    # 设置24小时CD
    await r.setex(cd_key, 86400, "1")

    await add_points(owner_user_id, 5, session=session)

    return True, (
        f"产品「{product.name}」升级到v{product.version}! "
        f"日收入+{actual_boost} → {fmt_traffic(product.daily_income)}"
    )

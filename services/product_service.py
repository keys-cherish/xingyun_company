"""产品创建、迭代和管理。

修复要点：
- 产品迭代有每日CD（每个产品每天只能迭代1次）
- 产品收入有上限
- 产品名称重复检测
- 版本上限
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from config import settings
from db.models import Product
from services.research_service import get_completed_techs
from services.user_service import add_traffic, add_points

_products_data: dict | None = None

# 产品收入上限和版本上限
MAX_PRODUCT_DAILY_INCOME = 500_000
MAX_PRODUCT_VERSION = 50


def _load_products() -> dict:
    global _products_data
    if _products_data is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "products.json"
        with open(path, encoding="utf-8") as f:
            _products_data = json.load(f)
    return _products_data


async def get_available_product_templates(session: AsyncSession, company_id: int) -> list[dict]:
    """返回公司可创建的产品模板（基于已完成科研）。"""
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

    tmpl = templates[product_key]

    # 检查前置科研
    completed = set(await get_completed_techs(session, company_id))
    if tmpl["tech_id"] not in completed:
        return None, "需要先完成对应科研"

    # 产品名称
    name = custom_name.strip() if custom_name.strip() else tmpl["name"]
    if len(name) > 32:
        return None, "产品名称最长32字符"

    # 重名检测（同公司内）
    existing = await session.execute(
        select(Product).where(Product.company_id == company_id, Product.name == name)
    )
    if existing.scalar_one_or_none():
        return None, f"已存在同名产品「{name}」"

    # 扣除费用
    ok = await add_traffic(session, owner_user_id, -settings.product_create_cost)
    if not ok:
        return None, f"流量不足，需要{settings.product_create_cost}MB"

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

    return product, f"产品「{name}」打造成功! 日收入: {tmpl['base_daily_income']}MB"


async def upgrade_product(
    session: AsyncSession,
    product_id: int,
    owner_user_id: int,
) -> tuple[bool, str]:
    """升级产品：+版本 +收入 +品质。每个产品每天只能迭代1次。"""
    product = await session.get(Product, product_id)
    if product is None:
        return False, "产品不存在"

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

    cost = int(settings.product_upgrade_cost_base * (1.3 ** (product.version - 1)))
    ok = await add_traffic(session, owner_user_id, -cost)
    if not ok:
        return False, f"流量不足，升级需要{cost}MB"

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
        f"日收入+{actual_boost} → {product.daily_income}MB"
    )

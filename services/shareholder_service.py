"""股东/投资系统。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, Shareholder
from services.company_service import add_funds, get_company_valuation
from services.user_service import add_traffic

# 单次投资上限，防止溢出
MAX_SINGLE_INVESTMENT = 10_000_000


async def invest(
    session: AsyncSession,
    user_id: int,
    company_id: int,
    amount: int,
) -> tuple[bool, str]:
    """用户投资流量到公司以换取股份。"""
    if amount <= 0:
        return False, "投资金额必须大于0"
    if amount > MAX_SINGLE_INVESTMENT:
        return False, f"单次投资上限为{MAX_SINGLE_INVESTMENT}MB"

    company = await session.get(Company, company_id)
    if company is None:
        return False, "公司不存在"

    # 扣除流量
    ok = await add_traffic(session, user_id, -amount)
    if not ok:
        return False, "流量不足"

    # 计算新股份
    valuation = await get_company_valuation(session, company)
    if valuation <= 0:
        valuation = 1
    new_shares_pct = min((amount / valuation) * 100, 200.0)  # 上限200%防止极端情况

    # 检查老板最低持股约束
    owner_sh = await _get_shareholder(session, company.id, company.owner_id)
    if owner_sh and user_id != company.owner_id:
        total_after = 100.0 + new_shares_pct
        new_owner_pct = (owner_sh.shares / total_after) * 100
        if new_owner_pct < settings.min_owner_share_pct:
            # 回滚流量
            await add_traffic(session, user_id, amount)
            max_invest = _max_investable(valuation, owner_sh.shares)
            return False, f"此投资会导致老板持股低于{settings.min_owner_share_pct}%，最多可投资{max_invest}MB"

    # 按比例稀释所有现有股东
    result = await session.execute(
        select(Shareholder).where(Shareholder.company_id == company_id)
    )
    existing = list(result.scalars().all())
    total_after = 100.0 + new_shares_pct

    for sh in existing:
        sh.shares = (sh.shares / total_after) * 100

    # 计算投资者获得的股份（归一化后）
    investor_new_shares = (new_shares_pct / total_after) * 100

    # 查找是否已是股东
    investor_sh = None
    for sh in existing:
        if sh.user_id == user_id:
            investor_sh = sh
            break

    if investor_sh:
        investor_sh.shares += investor_new_shares
        investor_sh.invested_amount += amount
    else:
        investor_sh = Shareholder(
            company_id=company_id,
            user_id=user_id,
            shares=investor_new_shares,
            invested_amount=amount,
        )
        session.add(investor_sh)

    # 验证股份总和（防护性检查）
    total_check = sum(sh.shares for sh in existing) + (investor_new_shares if investor_sh not in existing else 0)
    if abs(total_check - 100.0) > 0.01:
        # 强制归一化
        all_holders = list(existing)
        if investor_sh not in existing:
            all_holders.append(investor_sh)
        factor = 100.0 / total_check if total_check > 0 else 1.0
        for sh in all_holders:
            sh.shares *= factor

    # 添加资金到公司
    fund_ok = await add_funds(session, company_id, amount)
    if not fund_ok:
        # 回滚流量
        await add_traffic(session, user_id, amount)
        return False, "公司资金更新失败，请重试"

    await session.flush()
    return True, f"投资成功! 获得{investor_sh.shares:.2f}%股份"


async def get_shareholders(session: AsyncSession, company_id: int) -> list[Shareholder]:
    result = await session.execute(
        select(Shareholder).where(Shareholder.company_id == company_id).order_by(Shareholder.shares.desc())
    )
    return list(result.scalars().all())


async def _get_shareholder(session: AsyncSession, company_id: int, user_id: int) -> Shareholder | None:
    result = await session.execute(
        select(Shareholder).where(
            Shareholder.company_id == company_id,
            Shareholder.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


def _max_investable(valuation: int, owner_current_shares: float) -> int:
    """计算非老板最大可投资金额（不违反老板最低持股约束）。"""
    min_pct = settings.min_owner_share_pct
    if min_pct <= 0:
        min_pct = 1  # 防止除零
    max_new_pct = (owner_current_shares * 100 / min_pct) - 100
    if max_new_pct <= 0:
        return 0
    return int(max_new_pct / 100 * valuation)

"""Daily settlement: calculates income, distributes dividends, generates reports."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import Company, DailyReport, Product
from services.company_service import add_funds, update_daily_revenue, get_company_type_info
from services.cooperation_service import get_cooperation_bonus
from services.dividend_service import distribute_dividends
from services.realestate_service import get_total_estate_income
from services.random_events import roll_daily_events
from services.research_service import check_and_complete_research
from cache.redis_client import update_leaderboard
from utils.formatters import reputation_buff_multiplier

logger = logging.getLogger(__name__)


async def settle_company(session: AsyncSession, company: Company) -> tuple[DailyReport | None, list[str]]:
    """Run daily settlement for one company."""
    today = dt.date.today().isoformat()

    # Check for completed research
    completed_techs = await check_and_complete_research(session, company.id)
    if completed_techs:
        logger.info("Company %s completed research: %s", company.name, completed_techs)

    # Recalculate daily revenue
    await update_daily_revenue(session, company.id)
    await session.refresh(company)

    # 1. Product income
    product_income = company.daily_revenue

    # 1b. Company level revenue bonus (permanent)
    from services.company_service import get_level_revenue_bonus
    level_revenue_bonus = get_level_revenue_bonus(company.level)

    # 2. Cooperation bonus (non-stackable, highest single bonus)
    coop_bonus_rate = await get_cooperation_bonus(session, company.id)
    cooperation_bonus = int(product_income * coop_bonus_rate)

    # 3. Real estate income
    realestate_income = await get_total_estate_income(session, company.id)

    # 4. Reputation buff (applied to base product_income, non-stackable)
    from db.models import User
    owner = await session.get(User, company.owner_id)
    rep_multiplier = reputation_buff_multiplier(owner.reputation) if owner else 1.0
    # Buff applies as extra income on top of product income (buff - 1.0)
    reputation_buff_income = int(product_income * (rep_multiplier - 1.0))

    # 5. Advertising boost
    from services.ad_service import get_ad_boost
    ad_boost_rate = await get_ad_boost(company.id)
    ad_boost_income = int(product_income * ad_boost_rate)

    # 5b. Shop buff (market_analysis): product income boost
    from services.shop_service import get_income_buff_multiplier
    shop_buff_mult = await get_income_buff_multiplier(company.id)
    shop_buff_income = int(product_income * (shop_buff_mult - 1.0))

    # 6. Company type buff (收入加成)
    type_info = get_company_type_info(company.company_type)
    type_income_bonus = type_info.get("income_bonus", 0.0) if type_info else 0.0
    type_income = int(product_income * type_income_bonus)

    # Total gross
    total_income = product_income + level_revenue_bonus + cooperation_bonus + realestate_income + reputation_buff_income + ad_boost_income + shop_buff_income + type_income

    # Tax (on gross income)
    tax = int(total_income * settings.tax_rate)

    # Employee salary + social insurance
    salary_cost = company.employee_count * settings.employee_salary_base
    social_insurance = int(salary_cost * settings.social_insurance_rate)

    # Company type cost buff
    type_cost_bonus = type_info.get("cost_bonus", 0.0) if type_info else 0.0

    # Operating cost = base overhead + tax + salary + insurance, modified by type cost buff
    base_operating = int(total_income * settings.daily_operating_cost_pct) + salary_cost + social_insurance
    base_operating = int(base_operating * (1.0 + type_cost_bonus))  # cost_bonus < 0 means cheaper
    operating_cost = base_operating + tax
    profit = total_income - operating_cost

    # Apply net profit/loss to company funds.
    if profit != 0:
        success = await add_funds(session, company.id, profit)
        if not success and profit < 0:
            # 亏损超过现有资金，将资金清零
            old_ver = company.version
            await session.execute(
                update(Company)
                .where(Company.id == company.id, Company.version == old_ver)
                .values(total_funds=0, version=Company.version + 1)
            )
            await session.refresh(company)

    # Distribute dividends
    distributions = await distribute_dividends(session, company, profit)
    total_dividend = sum(amt for _, amt in distributions)

    # Roll random events
    event_messages = await roll_daily_events(session, company)

    # Generate report
    report = DailyReport(
        company_id=company.id,
        date=today,
        product_income=product_income,
        cooperation_bonus=cooperation_bonus,
        realestate_income=realestate_income,
        reputation_buff_income=reputation_buff_income,
        total_income=total_income,
        operating_cost=operating_cost,
        dividend_paid=total_dividend,
    )
    session.add(report)
    await session.flush()

    # 更新排行榜（多维度）
    await update_leaderboard("revenue", company.name, total_income)
    await update_leaderboard("funds", company.name, company.total_funds)
    valuation = int(
        company.total_funds * settings.valuation_fund_coeff
        + company.daily_revenue * settings.valuation_income_days
    )
    await update_leaderboard("valuation", company.name, valuation)

    return report, event_messages


async def settle_all(session: AsyncSession) -> list[tuple[Company, DailyReport, list[str]]]:
    """Run daily settlement for all companies."""
    # Run data integrity checks before settlement
    from services.integrity_service import run_all_checks
    integrity_msgs = await run_all_checks(session)
    if integrity_msgs:
        logger.info("Integrity checks: %s", integrity_msgs)

    result = await session.execute(select(Company))
    companies = list(result.scalars().all())
    reports = []
    for company in companies:
        try:
            report, events = await settle_company(session, company)
            if report:
                reports.append((company, report, events))
        except Exception:
            logger.exception("Settlement failed for company %s", company.name)
    return reports


def format_daily_report(company: Company, report: DailyReport, events: list[str] | None = None) -> str:
    """Format a daily report for display."""
    from utils.formatters import fmt_traffic
    profit = report.total_income - report.operating_cost
    lines = [
        f"📊 【{company.name}】每日结算报告",
        f"日期: {report.date}",
        f"{'─' * 24}",
        f"产品收入: {fmt_traffic(report.product_income)}",
        f"合作加成: +{fmt_traffic(report.cooperation_bonus)}",
        f"地产收入: +{fmt_traffic(report.realestate_income)}",
        f"声望加成: +{fmt_traffic(report.reputation_buff_income)}",
        f"{'─' * 24}",
        f"总收入: {fmt_traffic(report.total_income)}",
        f"运营成本(含税/薪/社保): -{fmt_traffic(report.operating_cost)}",
        f"净利润: {fmt_traffic(profit)}",
        f"分红支出: {fmt_traffic(report.dividend_paid)}",
        f"{'─' * 24}",
    ]
    if events:
        lines.append("🎲 今日事件:")
        lines.extend(events)
    return "\n".join(lines)

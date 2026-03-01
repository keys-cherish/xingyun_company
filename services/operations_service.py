"""Company operations gameplay: work-hour, office, training, insurance, culture, ethics, regulation."""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Company, CompanyOperationProfile
from utils.timezone import BJ_TZ
from cache.redis_client import get_redis


WORK_HOUR_OPTIONS: dict[int, dict] = {
    6: {"label": "轻松", "income_mult": 0.88, "cost_mult": 0.92, "ethics_delta": 1},
    8: {"label": "正常", "income_mult": 1.00, "cost_mult": 1.00, "ethics_delta": 0},
    10: {"label": "冲刺", "income_mult": 1.15, "cost_mult": 1.10, "ethics_delta": -1},
    12: {"label": "高压", "income_mult": 1.28, "cost_mult": 1.25, "ethics_delta": -3},
}

OFFICE_LEVELS: dict[str, dict] = {
    "basic": {"name": "基础办公", "income_mult": 1.00, "employee_cost": 6},
    "standard": {"name": "标准办公", "income_mult": 1.12, "employee_cost": 14},
    "premium": {"name": "优质办公", "income_mult": 1.35, "employee_cost": 26},
    "top": {"name": "顶级办公", "income_mult": 1.70, "employee_cost": 45},
}

TRAINING_LEVELS: dict[str, dict] = {
    "none": {"name": "无培训", "income_mult": 1.00, "hourly_cost": 0, "duration_hours": 0},
    "basic": {"name": "基础培训", "income_mult": 1.12, "hourly_cost": 30, "duration_hours": 24},
    "pro": {"name": "岗位实训", "income_mult": 1.30, "hourly_cost": 60, "duration_hours": 36},
    "elite": {"name": "精英特训", "income_mult": 1.50, "hourly_cost": 120, "duration_hours": 48},
}

INSURANCE_LEVELS: dict[str, dict] = {
    "basic": {"name": "基础险", "cost_rate": 0.010, "fine_reduction": 0.0},
    "plus": {"name": "进阶险", "cost_rate": 0.022, "fine_reduction": 0.4},
    "supreme": {"name": "至尊险", "cost_rate": 0.040, "fine_reduction": 0.8},
}

MARKET_TRENDS: tuple[dict, ...] = (
    {"key": "sun", "label": "☀️ 繁荣", "income_mult": 1.12},
    {"key": "cloud", "label": "⛅ 平稳", "income_mult": 1.00},
    {"key": "rain", "label": "🌧️ 衰退", "income_mult": 0.88},
)
_MARKET_CYCLE_ANCHOR = dt.date(2026, 1, 1)


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def ethics_rating(ethics: int) -> str:
    if ethics >= 90:
        return "守正"
    if ethics >= 70:
        return "良性"
    if ethics >= 45:
        return "中立"
    return "高风险"


def reputation_rating(reputation: int) -> str:
    if reputation >= 1000:
        return "SSS"
    if reputation >= 700:
        return "SS"
    if reputation >= 500:
        return "S"
    if reputation >= 300:
        return "A"
    if reputation >= 180:
        return "B"
    return "C"


def bar10(value: int, maximum: int = 100) -> str:
    blocks = int(round(_clamp(value, 0, maximum) / maximum * 10))
    return "█" * blocks + "░" * (10 - blocks)


async def get_or_create_profile(session: AsyncSession, company_id: int) -> CompanyOperationProfile:
    profile = await session.get(CompanyOperationProfile, company_id)
    if profile is None:
        profile = CompanyOperationProfile(company_id=company_id)
        session.add(profile)
        await session.flush()
    return profile


def _is_training_active(profile: CompanyOperationProfile, now: dt.datetime) -> bool:
    if profile.training_level == "none" or profile.training_expires_at is None:
        return False
    end_time = profile.training_expires_at
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=dt.UTC)
    return end_time > now


def get_market_trend(company: Company, now: dt.datetime | None = None) -> dict:
    """7-day market trend cycle with per-industry weekly randomness."""
    if now is None:
        now = dt.datetime.now(BJ_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt.UTC).astimezone(BJ_TZ)
    else:
        now = now.astimezone(BJ_TZ)

    cycle_week = ((now.date() - _MARKET_CYCLE_ANCHOR).days // 7)
    industry_key = company.company_type or "tech"
    rng = random.Random(f"{industry_key}:{cycle_week}")
    # 平稳概率略高，繁荣/衰退次之；每周每行业固定，下一周自动变化。
    return rng.choices(
        MARKET_TRENDS,
        weights=[28, 44, 28],
        k=1,
    )[0]


def get_training_info(profile: CompanyOperationProfile, now: dt.datetime) -> dict:
    info = TRAINING_LEVELS.get(profile.training_level, TRAINING_LEVELS["none"])
    active = _is_training_active(profile, now)
    return {
        "key": profile.training_level,
        "name": info["name"],
        "income_mult": info["income_mult"] if active else 1.0,
        "hourly_cost": info["hourly_cost"] if active else 0,
        "active": active,
        "expires_at": profile.training_expires_at,
    }


def get_operation_multipliers(profile: CompanyOperationProfile, now: dt.datetime) -> dict:
    work = WORK_HOUR_OPTIONS.get(profile.work_hours, WORK_HOUR_OPTIONS[8])
    office = OFFICE_LEVELS.get(profile.office_level, OFFICE_LEVELS["standard"])
    training = get_training_info(profile, now)
    culture_bonus = 1.0 + (profile.culture / 1000)  # max +10%
    return {
        "work": work,
        "office": office,
        "training": training,
        "culture_bonus_mult": culture_bonus,
        "income_mult": work["income_mult"] * office["income_mult"] * training["income_mult"] * culture_bonus,
    }


def calc_extra_operating_costs(
    profile: CompanyOperationProfile,
    employee_count: int,
    base_income: int,
    salary_cost: int,
    social_insurance: int,
    now: dt.datetime,
) -> dict:
    work = WORK_HOUR_OPTIONS.get(profile.work_hours, WORK_HOUR_OPTIONS[8])
    office = OFFICE_LEVELS.get(profile.office_level, OFFICE_LEVELS["standard"])
    training = get_training_info(profile, now)
    insurance = INSURANCE_LEVELS.get(profile.insurance_level, INSURANCE_LEVELS["basic"])

    office_cost = employee_count * int(office["employee_cost"])
    training_cost = employee_count * int(training["hourly_cost"]) if training["active"] else 0
    regulation_cost = int(base_income * (0.010 + profile.regulation_pressure / 5000))
    insurance_cost = int((salary_cost + social_insurance) * insurance["cost_rate"])
    work_cost_adjust = int((salary_cost + social_insurance) * (work["cost_mult"] - 1.0))
    culture_maintenance = int(base_income * (profile.culture / 20000))  # 0~0.5%

    return {
        "office_cost": max(0, office_cost),
        "training_cost": max(0, training_cost),
        "regulation_cost": max(0, regulation_cost),
        "insurance_cost": max(0, insurance_cost),
        "work_cost_adjust": work_cost_adjust,
        "culture_maintenance": max(0, culture_maintenance),
    }


def maybe_regulation_fine(
    profile: CompanyOperationProfile,
    income_total: int,
    now: dt.datetime,
) -> int:
    insurance = INSURANCE_LEVELS.get(profile.insurance_level, INSURANCE_LEVELS["basic"])
    base_risk = 0.02 + max(0, 50 - profile.ethics) * 0.004 + profile.regulation_pressure * 0.0005
    culture_risk_reduce = (profile.culture / 100) * 0.30
    risk = max(0.01, min(0.85, base_risk * (1.0 - culture_risk_reduce)))
    rng = random.Random((now.toordinal() * 97) + profile.company_id)
    if rng.random() > risk:
        return 0
    fine_base = int(income_total * (0.015 + max(0, 45 - profile.ethics) / 1000))
    return max(0, int(fine_base * (1.0 - insurance["fine_reduction"])))


async def settle_profile_daily(
    session: AsyncSession,
    profile: CompanyOperationProfile,
    now: dt.datetime,
):
    work = WORK_HOUR_OPTIONS.get(profile.work_hours, WORK_HOUR_OPTIONS[8])
    profile.ethics = _clamp(profile.ethics + int(work["ethics_delta"]), 0, 100)
    if profile.culture > 55:
        profile.ethics = _clamp(profile.ethics + 1, 0, 100)
    if profile.training_level != "none" and not _is_training_active(profile, now):
        profile.training_level = "none"
        profile.training_expires_at = None
    await session.flush()


async def set_work_hours(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
    hours: int,
) -> tuple[bool, str]:
    company = await session.get(Company, company_id)
    if company is None:
        return False, "公司不存在"
    if company.owner_id != owner_user_id:
        return False, "只有公司老板可以调整工时"
    if hours not in WORK_HOUR_OPTIONS:
        return False, "无效工时选项"
    profile = await get_or_create_profile(session, company_id)
    profile.work_hours = hours
    await session.flush()
    return True, f"已调整工时为 {hours}h（{WORK_HOUR_OPTIONS[hours]['label']}）"


async def cycle_option(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
    field: str,
) -> tuple[bool, str]:
    company = await session.get(Company, company_id)
    if company is None:
        return False, "公司不存在"
    if company.owner_id != owner_user_id:
        return False, "只有公司老板可操作"
    profile = await get_or_create_profile(session, company_id)

    if field == "office":
        keys = list(OFFICE_LEVELS.keys())
        idx = keys.index(profile.office_level) if profile.office_level in keys else 0
        if idx >= len(keys) - 1:
            return True, "已是顶级办公，无需继续升级"
        profile.office_level = keys[idx + 1]
        await session.flush()
        return True, f"办公升级为：{OFFICE_LEVELS[profile.office_level]['name']}"

    if field == "insurance":
        keys = list(INSURANCE_LEVELS.keys())
        idx = keys.index(profile.insurance_level) if profile.insurance_level in keys else 0
        profile.insurance_level = keys[(idx + 1) % len(keys)]
        await session.flush()
        return True, f"保险方案切换为：{INSURANCE_LEVELS[profile.insurance_level]['name']}"

    if field == "culture":
        profile.culture = _clamp(profile.culture + 8, 0, 100)
        await session.flush()
        return True, f"企业文化提升至 {profile.culture}/100"

    if field == "ethics":
        profile.ethics = _clamp(profile.ethics + 6, 0, 100)
        await session.flush()
        return True, f"企业道德提升至 {profile.ethics}/100"

    if field == "regulation":
        profile.regulation_pressure = _clamp(profile.regulation_pressure + 8, 0, 100)
        await session.flush()
        return True, f"监管强度调整为 {profile.regulation_pressure}/100"

    return False, "未知选项"


async def start_training(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
    level: str,
) -> tuple[bool, str]:
    from services.company_service import add_funds

    company = await session.get(Company, company_id)
    if company is None:
        return False, "公司不存在"
    if company.owner_id != owner_user_id:
        return False, "只有公司老板可开启培训"
    if level not in TRAINING_LEVELS:
        return False, "无效培训档位"
    if level == "none":
        profile = await get_or_create_profile(session, company_id)
        profile.training_level = "none"
        profile.training_expires_at = None
        await session.flush()
        return True, "已取消培训"

    profile = await get_or_create_profile(session, company_id)
    info = TRAINING_LEVELS[level]
    total_cost = int(company.employee_count * info["hourly_cost"] * info["duration_hours"])
    ok = await add_funds(session, company_id, -total_cost)
    if not ok:
        return False, f"公司资金不足，培训需要 {total_cost:,} 积分"
    now = dt.datetime.utcnow()
    profile.training_level = level
    profile.training_expires_at = now + dt.timedelta(hours=info["duration_hours"])
    profile.culture = _clamp(profile.culture + 4, 0, 100)
    await session.flush()
    end_bj = profile.training_expires_at.replace(tzinfo=dt.UTC).astimezone(BJ_TZ).strftime("%m-%d %H:%M")
    return True, f"培训已启动：{info['name']}，到期时间（北京时间）{end_bj}"


async def save_recent_events(company_id: int, events: list[str]):
    if not events:
        return
    r = await get_redis()
    key = f"company:events:{company_id}"
    trimmed = [e.replace("\n", " ").strip()[:120] for e in events[:8]]
    await r.delete(key)
    if trimmed:
        await r.rpush(key, *trimmed)
    await r.expire(key, 7 * 24 * 3600)


async def load_recent_events(company_id: int, limit: int = 3) -> list[str]:
    r = await get_redis()
    key = f"company:events:{company_id}"
    items = await r.lrange(key, 0, max(0, limit - 1))
    return [i for i in items if i]

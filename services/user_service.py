"""User registration, currency management, points, and quota exchange."""

from __future__ import annotations

import datetime as dt
import hashlib

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from config import settings
from db.models import User
from utils.formatters import CURRENCY_NAME, fmt_currency, fmt_quota


async def get_or_create_user(session: AsyncSession, tg_id: int, tg_name: str) -> tuple[User, bool]:
    """Return (user, created). If new, grant initial currency."""
    stmt = select(User).where(User.tg_id == tg_id)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user:
        if user.tg_name != tg_name:
            user.tg_name = tg_name
            await session.flush()
        return user, False

    user = User(tg_id=tg_id, tg_name=tg_name, traffic=settings.initial_traffic)
    session.add(user)
    await session.flush()
    return user, True


async def get_user_by_tg_id(session: AsyncSession, tg_id: int) -> User | None:
    result = await session.execute(select(User).where(User.tg_id == tg_id))
    return result.scalar_one_or_none()


async def add_traffic(session: AsyncSession, user_id: int, amount: int) -> bool:
    """Atomically add/subtract user currency (stored in legacy traffic field)."""
    for _retry in range(3):
        user = await session.get(User, user_id)
        if user is None:
            return False
        if amount < 0 and user.traffic + amount < 0:
            return False
        old_version = user.version
        result = await session.execute(
            update(User)
            .where(User.id == user_id, User.version == old_version)
            .values(traffic=User.traffic + amount, version=User.version + 1)
        )
        if result.rowcount > 0:
            await session.refresh(user)
            return True
        await session.refresh(user)
    return False


async def add_reputation(session: AsyncSession, user_id: int, amount: int) -> bool:
    """Add reputation to a user."""
    user = await session.get(User, user_id)
    if user is None:
        return False
    user.reputation += amount
    await session.flush()
    return True


# ---------- Points ----------

async def get_points(tg_id: int) -> int:
    r = await get_redis()
    val = await r.get(f"points:{tg_id}")
    return int(val) if val else 0


async def add_points(tg_id_or_user_id: int, amount: int, *, session: AsyncSession | None = None) -> int:
    """Add points and return new total."""
    tg_id = tg_id_or_user_id
    if session is not None:
        user = await session.get(User, tg_id_or_user_id)
        if user is None:
            return 0
        tg_id = user.tg_id
    r = await get_redis()
    return await r.incrby(f"points:{tg_id}", amount)


# ---------- Quota exchange (currency -> MB) ----------

BASE_CREDIT_TO_QUOTA_RATE = 120  # 120 金币 = 1MB quota (base)
RATE_VOLATILITY = 0.20  # +/-20% hourly fluctuation
STREAK_BONUS_EVERY = 3  # every 3 successful exchanges per day
STREAK_BONUS_MB = 1


def get_credit_to_quota_rate(tg_id: int) -> int:
    """Get hourly dynamic rate: how many 金币 are needed for 1MB quota."""
    hour_tag = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H")
    seed = f"{tg_id}:{hour_tag}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    ratio = digest[0] / 255  # 0.0 ~ 1.0
    factor = (1.0 - RATE_VOLATILITY) + ratio * (2 * RATE_VOLATILITY)
    return max(1, int(round(BASE_CREDIT_TO_QUOTA_RATE * factor)))


async def get_quota_mb(tg_id: int) -> int:
    """Current user quota balance (MB), stored in Redis."""
    r = await get_redis()
    val = await r.get(f"quota:{tg_id}")
    return int(val) if val else 0


async def add_quota_mb(tg_id: int, amount_mb: int) -> int:
    """Add quota and return latest balance."""
    if amount_mb <= 0:
        return await get_quota_mb(tg_id)
    r = await get_redis()
    return await r.incrby(f"quota:{tg_id}", amount_mb)


async def _calc_exchange_bonus_mb(tg_id: int) -> int:
    """Daily streak bonus: every Nth exchange grants extra quota."""
    r = await get_redis()
    today = dt.date.today().isoformat()
    key = f"quota_exchange_count:{tg_id}:{today}"
    count = await r.incr(key)
    if count == 1:
        await r.expire(key, 172800)  # keep 2 days
    if count % STREAK_BONUS_EVERY == 0:
        return STREAK_BONUS_MB
    return 0


async def exchange_credits_for_quota(
    session: AsyncSession,
    tg_id: int,
    credits_amount: int,
) -> tuple[bool, str]:
    """Exchange currency to quota with dynamic rates and streak rewards."""
    if credits_amount <= 0:
        return False, "兑换数量必须大于 0"

    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return False, "用户不存在"

    rate = get_credit_to_quota_rate(tg_id)
    quota_gained = credits_amount // rate
    if quota_gained <= 0:
        return False, f"当前汇率下，至少需要 {rate} {CURRENCY_NAME} 才能兑换 1MB 额度"

    actual_cost = quota_gained * rate
    ok = await add_traffic(session, user.id, -actual_cost)
    if not ok:
        return False, f"{CURRENCY_NAME}不足，兑换 {fmt_quota(quota_gained)} 需要 {fmt_currency(actual_cost)}"

    bonus_mb = await _calc_exchange_bonus_mb(tg_id)
    total_quota = quota_gained + bonus_mb
    await add_quota_mb(tg_id, total_quota)

    if bonus_mb > 0:
        return True, (
            f"兑换成功！消耗 {fmt_currency(actual_cost)}，获得 {fmt_quota(quota_gained)}，"
            f"连兑奖励 +{bonus_mb}MB，合计 {fmt_quota(total_quota)}。"
        )
    return True, f"兑换成功！消耗 {fmt_currency(actual_cost)}，获得 {fmt_quota(total_quota)}。"


# ---------- Backward compatibility: points -> currency ----------

POINTS_TO_TRAFFIC_RATE = 10  # keep constant name for compatibility

_DEDUCT_POINTS_LUA = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', key) or '0')
if current < amount then
    return -1
end
return redis.call('DECRBY', key, amount)
"""


async def exchange_points_for_traffic(session: AsyncSession, tg_id: int, points_amount: int) -> tuple[bool, str]:
    """Exchanges points to currency."""
    if points_amount <= 0:
        return False, "兑换积分数必须大于 0"

    credits_gained = points_amount // POINTS_TO_TRAFFIC_RATE
    if credits_gained <= 0:
        return False, f"至少需要 {POINTS_TO_TRAFFIC_RATE} 积分才能兑换 1 {CURRENCY_NAME}"

    actual_points_used = credits_gained * POINTS_TO_TRAFFIC_RATE
    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return False, "用户不存在"

    r = await get_redis()
    result = await r.eval(_DEDUCT_POINTS_LUA, 1, f"points:{tg_id}", actual_points_used)
    if result == -1:
        return False, "积分不足"

    ok = await add_traffic(session, user.id, credits_gained)
    if not ok:
        await r.incrby(f"points:{tg_id}", actual_points_used)
        return False, f"{CURRENCY_NAME}添加失败，请重试"
    return True, f"兑换成功！消耗 {actual_points_used} 积分，获得 {credits_gained:,} {CURRENCY_NAME}"


# ---------- Reverse exchange: quota -> currency ----------

REVERSE_EXCHANGE_PENALTY = 0.20  # 20% loss on reverse exchange

_DEDUCT_QUOTA_LUA = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', key) or '0')
if current < amount then
    return -1
end
return redis.call('DECRBY', key, amount)
"""


async def exchange_quota_for_credits(
    session: AsyncSession,
    tg_id: int,
    quota_mb: int,
) -> tuple[bool, str]:
    """Exchange quota (MB) back to currency at a 20% penalty."""
    if quota_mb <= 0:
        return False, "兑换额度必须大于 0"

    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return False, "用户不存在"

    rate = get_credit_to_quota_rate(tg_id)
    reverse_rate = int(rate * (1.0 - REVERSE_EXCHANGE_PENALTY))
    credits_gained = quota_mb * reverse_rate
    if credits_gained <= 0:
        return False, "兑换金额过小"

    # Atomically deduct quota from Redis
    r = await get_redis()
    result = await r.eval(_DEDUCT_QUOTA_LUA, 1, f"quota:{tg_id}", quota_mb)
    if result == -1:
        return False, f"额度不足，当前 {fmt_quota(await get_quota_mb(tg_id))}"

    ok = await add_traffic(session, user.id, credits_gained)
    if not ok:
        # Rollback quota
        await r.incrby(f"quota:{tg_id}", quota_mb)
        return False, f"{CURRENCY_NAME}添加失败，请重试"

    return True, f"兑换成功！消耗 {fmt_quota(quota_mb)}，获得 {fmt_currency(credits_gained)}"

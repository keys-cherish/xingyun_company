"""User registration, traffic management, and points system."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import User


async def get_or_create_user(session: AsyncSession, tg_id: int, tg_name: str) -> tuple[User, bool]:
    """Return (user, created). If new, grant initial traffic."""
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
    """原子性增减流量。使用乐观锁，失败最多重试3次。"""
    for _retry in range(3):
        user = await session.get(User, user_id)
        if user is None:
            return False
        if amount < 0 and user.traffic + amount < 0:
            return False  # 余额不足
        old_version = user.version
        result = await session.execute(
            update(User)
            .where(User.id == user_id, User.version == old_version)
            .values(traffic=User.traffic + amount, version=User.version + 1)
        )
        if result.rowcount > 0:
            # 使对象过期，下次访问时从DB重新加载，避免重复计数
            session.expire(user)
            return True
        # 并发冲突，刷新后重试
        await session.refresh(user)
    return False  # 3次重试均失败


async def add_reputation(session: AsyncSession, user_id: int, amount: int) -> bool:
    """Add reputation to a user."""
    user = await session.get(User, user_id)
    if user is None:
        return False
    user.reputation += amount
    await session.flush()
    return True


# ---------- Points / 积分 system ----------
# Points are stored in Redis for fast access; can be exchanged for traffic.
# Key: points:{tg_id}

from cache.redis_client import get_redis


async def get_points(tg_id: int) -> int:
    r = await get_redis()
    val = await r.get(f"points:{tg_id}")
    return int(val) if val else 0


async def add_points(tg_id_or_user_id: int, amount: int, *, session: AsyncSession | None = None) -> int:
    """Add points and return new total.

    tg_id_or_user_id: 如果传入session，则视为user_id（内部ID），自动查询tg_id。
    否则视为tg_id直接使用。
    """
    tg_id = tg_id_or_user_id
    if session is not None:
        user = await session.get(User, tg_id_or_user_id)
        if user is None:
            return 0
        tg_id = user.tg_id
    r = await get_redis()
    return await r.incrby(f"points:{tg_id}", amount)


POINTS_TO_TRAFFIC_RATE = 10  # 10 points = 1 traffic


async def exchange_points_for_traffic(session: AsyncSession, tg_id: int, points_amount: int) -> tuple[bool, str]:
    """Exchange points for traffic. Returns (success, message)."""
    if points_amount <= 0:
        return False, "兑换积分数必须大于0"
    current = await get_points(tg_id)
    if current < points_amount:
        return False, f"积分不足，当前积分: {current}"

    traffic_gained = points_amount // POINTS_TO_TRAFFIC_RATE
    if traffic_gained <= 0:
        return False, f"至少需要{POINTS_TO_TRAFFIC_RATE}积分才能兑换1MB"

    actual_points_used = traffic_gained * POINTS_TO_TRAFFIC_RATE
    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return False, "用户不存在"

    r = await get_redis()
    await r.decrby(f"points:{tg_id}", actual_points_used)
    ok = await add_traffic(session, user.id, traffic_gained)
    if not ok:
        # rollback points
        await r.incrby(f"points:{tg_id}", actual_points_used)
        return False, "流量添加失败，请重试"
    return True, f"成功兑换! 消耗{actual_points_used}积分，获得{traffic_gained}MB"

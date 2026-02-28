"""Roadshow system - spend traffic for random rewards."""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from config import settings
from db.models import Company, Roadshow
from services.user_service import add_reputation, add_traffic, add_points

ROADSHOW_TYPES = ["技术展会", "投资峰会", "媒体发布会", "行业论坛"]

REWARD_TABLE = [
    {"weight": 30, "type": "traffic", "min": 200, "max": 800, "desc": "获得流量奖励"},
    {"weight": 25, "type": "reputation", "min": 3, "max": 15, "desc": "声望提升"},
    {"weight": 20, "type": "traffic", "min": 500, "max": 2000, "desc": "大额流量奖励"},
    {"weight": 15, "type": "points", "min": 10, "max": 50, "desc": "获得积分"},
    {"weight": 10, "type": "jackpot", "min": 2000, "max": 5000, "desc": "路演大成功! 巨额流量"},
]


async def can_roadshow(company_id: int) -> tuple[bool, int]:
    """Check cooldown. Returns (can_do, remaining_seconds)."""
    r = await get_redis()
    key = f"roadshow_cd:{company_id}"
    ttl = await r.ttl(key)
    if ttl > 0:
        return False, ttl
    return True, 0


async def do_roadshow(
    session: AsyncSession,
    company_id: int,
    owner_user_id: int,
) -> tuple[bool, str]:
    """Perform a roadshow."""
    can, remaining = await can_roadshow(company_id)
    if not can:
        return False, f"路演冷却中，还需{remaining}秒"

    # Deduct cost
    ok = await add_traffic(session, owner_user_id, -settings.roadshow_cost)
    if not ok:
        return False, f"流量不足，路演需要{settings.roadshow_cost}流量"

    # Random type
    rs_type = random.choice(ROADSHOW_TYPES)

    # Roll reward
    weights = [r["weight"] for r in REWARD_TABLE]
    reward = random.choices(REWARD_TABLE, weights=weights, k=1)[0]
    amount = random.randint(reward["min"], reward["max"])

    result_text = f"【{rs_type}】{reward['desc']}"
    bonus = 0
    rep_gained = 0

    if reward["type"] == "traffic" or reward["type"] == "jackpot":
        await add_traffic(session, owner_user_id, amount)
        bonus = amount
        result_text += f" +{amount}流量"
    elif reward["type"] == "reputation":
        await add_reputation(session, owner_user_id, amount)
        rep_gained = amount
        result_text += f" +{amount}声望"
    elif reward["type"] == "points":
        await add_points(owner_user_id, amount)
        result_text += f" +{amount}积分"

    # Base reputation gain for doing roadshow
    base_rep = 2
    await add_reputation(session, owner_user_id, base_rep)
    rep_gained += base_rep

    # Record
    roadshow = Roadshow(
        company_id=company_id,
        type=rs_type,
        result=result_text,
        bonus=bonus,
        reputation_gained=rep_gained,
    )
    session.add(roadshow)
    await session.flush()

    # Set cooldown
    r = await get_redis()
    await r.setex(f"roadshow_cd:{company_id}", settings.roadshow_cooldown_seconds, "1")

    # Points for roadshow
    await add_points(owner_user_id, 3)

    return True, result_text

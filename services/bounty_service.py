"""悬赏令系统 — 花声望悬赏其他公司，激活PvP生态。

Redis keys:
    bounty:{target_company_id} → JSON bounty data, TTL 86400s
    bounty_cd:{tg_id}          → cooldown marker, TTL 7200s
"""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from db.models import Company, User

logger = logging.getLogger(__name__)

BOUNTY_TTL = 86400  # 24h
BOUNTY_COOLDOWN = 7200  # 2h
BOUNTY_REPUTATION_COST = 80
BOUNTY_ATTACKS = 3
BOUNTY_POWER_BONUS = 0.15
BOUNTY_LOOT_BONUS = 0.50


async def post_bounty(
    session: AsyncSession,
    poster_tg_id: int,
    poster_company_id: int,
    target_company_id: int,
) -> tuple[bool, str]:
    """Post a bounty on a target company. Returns (success, message)."""
    from services.user_service import add_reputation

    r = await get_redis()

    # Cooldown check
    cd_key = f"bounty_cd:{poster_tg_id}"
    if await r.get(cd_key):
        ttl = await r.ttl(cd_key)
        mins = max(1, ttl // 60)
        return False, f"❌ 悬赏冷却中，还需 {mins} 分钟"

    # Can't bounty own company
    if poster_company_id == target_company_id:
        return False, "❌ 不能悬赏自己的公司"

    # Check target exists
    target = await session.get(Company, target_company_id)
    if target is None:
        return False, "❌ 目标公司不存在"

    # Check existing bounty
    bounty_key = f"bounty:{target_company_id}"
    if await r.get(bounty_key):
        return False, f"❌ 「{target.name}」已有悬赏令生效中"

    # Check poster has enough reputation
    poster_company = await session.get(Company, poster_company_id)
    if poster_company is None:
        return False, "❌ 你的公司不存在"

    poster_user = await session.get(User, poster_company.owner_id)
    if poster_user is None or poster_user.reputation < BOUNTY_REPUTATION_COST:
        return False, f"❌ 声望不足，悬赏需要 {BOUNTY_REPUTATION_COST} 声望"

    # Deduct reputation
    ok = await add_reputation(session, poster_company.owner_id, -BOUNTY_REPUTATION_COST)
    if not ok:
        return False, "❌ 声望扣除失败"

    # Write bounty
    data = json.dumps({
        "poster_tg_id": poster_tg_id,
        "poster_company_id": poster_company_id,
        "poster_company_name": poster_company.name,
        "target_company_name": target.name,
        "reputation_spent": BOUNTY_REPUTATION_COST,
        "attacks_remaining": BOUNTY_ATTACKS,
        "power_bonus": BOUNTY_POWER_BONUS,
        "loot_bonus": BOUNTY_LOOT_BONUS,
    })
    await r.set(bounty_key, data, ex=BOUNTY_TTL)
    await r.set(cd_key, "1", ex=BOUNTY_COOLDOWN)

    return True, (
        f"🎯 悬赏令已发布！\n"
        f"目标：「{target.name}」\n"
        f"奖励：攻击力+{int(BOUNTY_POWER_BONUS * 100)}% | 掠夺+{int(BOUNTY_LOOT_BONUS * 100)}%\n"
        f"有效攻击次数：{BOUNTY_ATTACKS}次\n"
        f"有效期：24小时\n"
        f"消耗声望：{BOUNTY_REPUTATION_COST}"
    )


async def get_active_bounty(target_company_id: int) -> dict | None:
    """Get active bounty on a target company, or None."""
    r = await get_redis()
    raw = await r.get(f"bounty:{target_company_id}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def check_bounty_bonus(target_company_id: int) -> tuple[float, float]:
    """Return (power_bonus, loot_bonus) if bounty is active on target."""
    bounty = await get_active_bounty(target_company_id)
    if not bounty or bounty.get("attacks_remaining", 0) <= 0:
        return 0.0, 0.0
    return bounty.get("power_bonus", 0.0), bounty.get("loot_bonus", 0.0)


async def consume_bounty_attack(target_company_id: int) -> bool:
    """Consume one bounty attack. Returns True if consumed, False if no bounty."""
    r = await get_redis()
    bounty_key = f"bounty:{target_company_id}"
    raw = await r.get(bounty_key)
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return False

    remaining = data.get("attacks_remaining", 0)
    if remaining <= 0:
        await r.delete(bounty_key)
        return False

    data["attacks_remaining"] = remaining - 1
    if data["attacks_remaining"] <= 0:
        await r.delete(bounty_key)
    else:
        ttl = await r.ttl(bounty_key)
        if ttl > 0:
            await r.set(bounty_key, json.dumps(data), ex=ttl)
        else:
            await r.set(bounty_key, json.dumps(data), ex=BOUNTY_TTL)
    return True


async def get_all_bounties() -> list[dict]:
    """Get all active bounties. Note: requires scan, use sparingly."""
    # This is a simple implementation; in production you'd use a SET index
    # For now, we won't implement scan — bounties are queried per-target
    return []

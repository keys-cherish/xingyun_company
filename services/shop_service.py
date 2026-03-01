"""Shop and black market service — item purchase, buff management, daily black market."""

from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path

from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from services.company_service import add_funds, get_company_by_id
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

_shop_items: dict | None = None


def load_shop_items() -> dict:
    global _shop_items
    if _shop_items is None:
        path = Path(__file__).resolve().parent.parent / "game_data" / "shop_items.json"
        with open(path, encoding="utf-8") as f:
            _shop_items = json.load(f)
    return _shop_items


async def has_buff(company_id: int, item_key: str) -> bool:
    """Check if a company has an active buff."""
    r = await get_redis()
    return await r.exists(f"buff:{company_id}:{item_key}") > 0


async def get_active_buffs(company_id: int) -> list[dict]:
    """Get all active shop buffs for a company."""
    r = await get_redis()
    items = load_shop_items()
    active = []
    for key, info in items.items():
        ttl = await r.ttl(f"buff:{company_id}:{key}")
        if ttl > 0:
            remaining = f"{ttl // 3600}时{(ttl % 3600) // 60}分"
            active.append({"key": key, "name": info["name"], "remaining": remaining, **info})
        elif ttl == -1:
            # Key exists with no TTL (one-time buff waiting to be consumed)
            active.append({"key": key, "name": info["name"], "remaining": "待触发", **info})
    return active


async def buy_item(
    session: AsyncSession,
    tg_id: int,
    company_id: int,
    item_key: str,
    price_override: int | None = None,
) -> tuple[bool, str]:
    """Purchase a shop item. Deducts from company funds, applies buff."""
    items = load_shop_items()
    if item_key not in items:
        return False, "无效的道具"

    item = items[item_key]
    price = price_override if price_override is not None else item["price"]

    # Check if buff already active
    if await has_buff(company_id, item_key):
        return False, f"{item['name']} 效果仍在生效中"

    # Precision marketing: check roadshow cooldown
    if item_key == "precision_marketing":
        from services.roadshow_service import can_roadshow
        can_do, remaining = await can_roadshow(company_id)
        if not can_do:
            mins = remaining // 60
            return False, f"路演冷却中（剩余{mins}分钟），精准营销暂时无法购买"

    user = await get_user_by_tg_id(session, tg_id)
    if user is None:
        return False, "用户不存在"
    company = await get_company_by_id(session, company_id)
    if company is None:
        return False, "公司不存在"
    if company.owner_id != user.id:
        return False, "无权操作"

    ok = await add_funds(session, company_id, -price)
    if not ok:
        return False, f"公司资金不足，需要 {fmt_traffic(price)}"

    # Apply buff
    r = await get_redis()
    buff_key = f"buff:{company_id}:{item_key}"

    if item.get("one_time"):
        # One-time buffs: stored without TTL, consumed on use
        await r.set(buff_key, "1")
    else:
        duration_seconds = item["duration_hours"] * 3600
        await r.setex(buff_key, duration_seconds, "1")

    # Handle immediate effects
    if item["effect"] == "research_speed":
        await _apply_research_speed(session, company_id)
        # Consume immediately
        await r.delete(buff_key)

    return True, f"购买成功! {item['name']}\n{item['description']}"


async def _apply_research_speed(session: AsyncSession, company_id: int):
    """Halve remaining research time for all in-progress research."""
    from services.research_service import get_in_progress_research, _load_tech_tree
    now = (await session.execute(select(sqlfunc.now()))).scalar()
    if now is None:
        now = dt.datetime.utcnow()
    if getattr(now, "tzinfo", None):
        now = now.replace(tzinfo=None)
    tech_tree = _load_tech_tree()
    in_progress = await get_in_progress_research(session, company_id)
    for rp in in_progress:
        started = rp.started_at.replace(tzinfo=None) if rp.started_at.tzinfo else rp.started_at
        duration_seconds = int(tech_tree.get(rp.tech_id, {}).get("duration_seconds", 3600))

        elapsed = max(0.0, (now - started).total_seconds())
        remaining = max(0.0, duration_seconds - elapsed)
        if remaining <= 1:
            continue

        # Halve remaining time: move started_at earlier by half of current remaining.
        shift_seconds = remaining / 2.0
        rp.started_at = rp.started_at - dt.timedelta(seconds=shift_seconds)
        await session.flush()


async def consume_buff(company_id: int, item_key: str) -> bool:
    """Consume a one-time buff. Returns True if buff existed and was consumed."""
    r = await get_redis()
    buff_key = f"buff:{company_id}:{item_key}"
    result = await r.delete(buff_key)
    return result > 0


async def get_income_buff_multiplier(company_id: int) -> float:
    """Get income multiplier from shop buffs (market_analysis)."""
    r = await get_redis()
    if await r.exists(f"buff:{company_id}:market_analysis"):
        items = load_shop_items()
        return 1.0 + items["market_analysis"]["effect_value"]
    return 1.0


async def should_skip_negative_event(company_id: int) -> bool:
    """Check if company has risk_hedge buff (skip negative events)."""
    return await has_buff(company_id, "risk_hedge")


async def get_roadshow_multiplier(company_id: int) -> float:
    """Check if precision_marketing buff is active; if so, consume and return 2x."""
    if await consume_buff(company_id, "precision_marketing"):
        return 2.0
    return 1.0


# ---------- Black Market ----------

async def generate_black_market():
    """Generate 1-2 daily black market deals (random shop items at discount)."""
    items = load_shop_items()
    keys = list(items.keys())
    count = random.choice([1, 2])
    deals = []

    selected = random.sample(keys, min(count, len(keys)))
    for key in selected:
        item = items[key]
        discount = random.uniform(0.30, 0.50)
        discounted_price = int(item["price"] * (1.0 - discount))
        deals.append({
            "item_key": key,
            "name": item["name"],
            "original_price": item["price"],
            "price": discounted_price,
            "discount_pct": int(discount * 100),
            "description": item["description"],
            "stock": random.randint(1, 3),
        })

    r = await get_redis()
    today = dt.date.today().isoformat()
    await r.set(f"blackmarket:{today}", json.dumps(deals, ensure_ascii=False))
    await r.expire(f"blackmarket:{today}", 172800)  # keep 2 days
    return deals


async def get_black_market_items() -> list[dict]:
    """Get today's black market items."""
    r = await get_redis()
    today = dt.date.today().isoformat()
    data = await r.get(f"blackmarket:{today}")
    if data:
        return json.loads(data)
    # Auto-generate if none exist
    return await generate_black_market()


async def buy_black_market_item(
    session: AsyncSession,
    tg_id: int,
    company_id: int,
    index: int,
) -> tuple[bool, str]:
    """Buy a black market item by index."""
    items = await get_black_market_items()
    if index < 0 or index >= len(items):
        return False, "无效的黑市商品"

    deal = items[index]
    if deal["stock"] <= 0:
        return False, f"{deal['name']} 已售罄"

    # Buy through regular shop with price override
    ok, msg = await buy_item(session, tg_id, company_id, deal["item_key"], price_override=deal["price"])
    if not ok:
        return False, msg

    # Decrease stock
    deal["stock"] -= 1
    r = await get_redis()
    today = dt.date.today().isoformat()
    await r.set(f"blackmarket:{today}", json.dumps(items, ensure_ascii=False))

    return True, f"黑市购买成功! {deal['name']} (省了 {deal['discount_pct']}%)"

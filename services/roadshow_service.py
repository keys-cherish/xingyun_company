"""Roadshow system - spend gold for random rewards with narrative flavor text."""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from config import settings
from db.models import Company, Roadshow
from services.company_service import add_funds
from services.user_service import add_reputation, add_points
from utils.formatters import fmt_traffic

ROADSHOW_TYPES = ["技术展会", "投资峰会", "媒体发布会", "行业论坛"]

REWARD_TABLE = [
    {"weight": 30, "type": "traffic", "min": 200, "max": 800, "desc": "获得积分奖励"},
    {"weight": 25, "type": "reputation", "min": 3, "max": 15, "desc": "声望提升"},
    {"weight": 20, "type": "traffic", "min": 500, "max": 2000, "desc": "大额积分奖励"},
    {"weight": 15, "type": "points", "min": 10, "max": 50, "desc": "获得积分"},
    {"weight": 10, "type": "jackpot", "min": 2000, "max": 5000, "desc": "路演大成功! 巨额积分"},
]

# ---- Narrative flavor text ----

STORIES_TRAFFIC = [
    "你的演讲征服了在场的投资人，多家基金当场表示合作意向！",
    "产品演示环节出现了意想不到的惊喜效果，观众掌声雷动，订单纷至沓来！",
    "你在台上侃侃而谈，一位神秘大佬悄悄塞过来一张支票...",
    "路演现场气氛热烈，你的商业计划书被投资人疯抢，资金涌入！",
    "演讲结束后，好几家企业主动来谈合作，你的邮箱被塞满了合同。",
]

STORIES_REPUTATION = [
    "你的演讲被媒体大量报道，行业内纷纷议论你的公司是下一匹黑马！",
    "路演中你展示的技术方案震惊全场，多家媒体争相采访。",
    "一位知名行业分析师在社交媒体上盛赞你的公司，粉丝暴涨！",
    "你的路演视频意外走红网络，公司知名度大幅提升。",
    "观众中有一位顶级KOL，他发了一条关于你的推荐帖，引发了行业热议。",
]

STORIES_POINTS = [
    "路演虽然反响平平，但你认识了一些有价值的行业人脉，经验值得积累。",
    "你在路演中遇到了一位老前辈，他的指点让你受益匪浅。",
    "这次路演规模不大，但细水长流，你获得了一些有用的行业洞察。",
    "现场来了一些行业记者，虽然没有大单，但积累了不少人脉资源。",
]

STORIES_JACKPOT = [
    "🎉 天降好运！台下坐着一位隐形富豪，他对你的项目一见钟情，当场签下巨额投资协议！",
    "🎉 你的路演引发了投资人之间的竞价大战，最终以远超预期的金额成交！",
    "🎉 一位跨国集团的CEO恰好路过会场，被你的演讲吸引驻足。他说：'这就是我一直在找的项目！'",
    "🎉 你的产品在路演现场引发轰动，媒体争相报道，多家顶级VC连夜发来投资意向书！",
]

STORIES_BY_TYPE = {
    "traffic": STORIES_TRAFFIC,
    "reputation": STORIES_REPUTATION,
    "points": STORIES_POINTS,
    "jackpot": STORIES_JACKPOT,
}


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
    """Perform a roadshow with narrative flavor text."""
    can, remaining = await can_roadshow(company_id)
    if not can:
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        return False, f"路演冷却中，还需 {hours}时{minutes}分"

    # Deduct cost from company funds
    ok = await add_funds(session, company_id, -settings.roadshow_cost)
    if not ok:
        return False, f"公司资金不足，路演需要 {fmt_traffic(settings.roadshow_cost)}"

    # Random type
    rs_type = random.choice(ROADSHOW_TYPES)

    # Roll reward
    weights = [r["weight"] for r in REWARD_TABLE]
    reward = random.choices(REWARD_TABLE, weights=weights, k=1)[0]
    amount = random.randint(reward["min"], reward["max"])

    # Check precision_marketing buff (roadshow double)
    from services.shop_service import get_roadshow_multiplier
    rs_multiplier = await get_roadshow_multiplier(company_id)
    if rs_multiplier > 1.0:
        amount = int(amount * rs_multiplier)

    # Pick a narrative story
    stories = STORIES_BY_TYPE.get(reward["type"], STORIES_TRAFFIC)
    story = random.choice(stories)

    bonus = 0
    rep_gained = 0
    reward_line = ""

    if reward["type"] == "traffic" or reward["type"] == "jackpot":
        await add_funds(session, company_id, amount)
        bonus = amount
        reward_line = f"💰 资金 +{fmt_traffic(amount)}"
    elif reward["type"] == "reputation":
        await add_reputation(session, owner_user_id, amount)
        rep_gained = amount
        reward_line = f"⭐ 声望 +{amount}"
    elif reward["type"] == "points":
        await add_points(owner_user_id, amount, session=session)
        reward_line = f"🎁 积分 +{amount}"

    if rs_multiplier > 1.0:
        reward_line += " (精准营销翻倍!)"

    # Base reputation gain for doing roadshow
    base_rep = 2
    await add_reputation(session, owner_user_id, base_rep)
    rep_gained += base_rep

    # Build narrative result
    result_text = (
        f"🎤 【{rs_type}】\n"
        f"{'─' * 24}\n"
        f"{story}\n"
        f"{'─' * 24}\n"
        f"{reward_line}\n"
        f"⭐ 基础声望 +{base_rep}"
    )

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
    await add_points(owner_user_id, 3, session=session)

    return True, result_text

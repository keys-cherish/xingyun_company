"""Business battle (商战) – auto PK between two companies."""

from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from db.models import Company, Product, ResearchProgress
from services.company_service import add_funds
from utils.formatters import fmt_traffic

# Cooldown: 1 battle per user every 30 minutes
BATTLE_COOLDOWN_SECONDS = 1800
# Loser pays this percentage of their funds to winner
LOOT_RATE = 0.05  # 5%
MIN_LOOT = 500
MAX_LOOT = 50000

# Winner taunts – {winner} = winner company name, {loser} = loser company name
_TAUNTS = [
    "「{winner}」笑着说：回去好好练练再来吧，「{loser}」不过如此！",
    "「{winner}」董事长发表声明：这场商战毫无悬念，建议「{loser}」考虑转行。",
    "「{winner}」的员工集体欢呼：老板威武！「{loser}」已被碾压！",
    "「{winner}」在朋友圈发了条动态：今天又赢了，对手「{loser}」太弱了，无聊。",
    "「{winner}」CEO淡定地喝了口咖啡：「{loser}」？不好意思，没听说过。",
    "「{winner}」官方公告：感谢「{loser}」的慷慨赞助，欢迎下次再来！",
    "「{winner}」HR部门：我们正在招聘，欢迎「{loser}」的前员工投递简历。",
    "「{winner}」市场部表示：这不是商战，这是降维打击。「{loser}」辛苦了。",
    "「{winner}」的股东们笑了：投资「{winner}」果然没错，「{loser}」不堪一击！",
    "「{winner}」前台小姐姐：刚才有个叫「{loser}」的来踢馆？已经被保安请走了。",
    "「{winner}」发布新闻稿：本次与「{loser}」的商业竞争已圆满结束，我方大获全胜。",
    "「{winner}」老板叼着雪茄：告诉「{loser}」，想翻身？下辈子吧。",
    "「{winner}」实习生都看不下去了：「{loser}」这水平，我一个人就能打十个。",
    "「{winner}」食堂今天加了鸡腿，庆祝打败「{loser}」！",
    "「{winner}」的扫地阿姨：又有人来送钱了？「{loser}」真是好人啊。",
]


def _pick_taunt(winner_name: str, loser_name: str) -> str:
    return random.choice(_TAUNTS).format(winner=winner_name, loser=loser_name)


async def _check_cooldown(tg_id: int) -> int:
    """Return remaining cooldown seconds, 0 if ready."""
    r = await get_redis()
    ttl = await r.ttl(f"battle_cd:{tg_id}")
    return max(0, ttl)


async def _set_cooldown(tg_id: int):
    r = await get_redis()
    await r.set(f"battle_cd:{tg_id}", "1", ex=BATTLE_COOLDOWN_SECONDS)


def _calc_battle_power(company: Company, product_count: int, tech_count: int) -> float:
    """Calculate overall battle power with randomness."""
    base = (
        company.total_funds * 0.3
        + company.daily_revenue * 30
        + company.employee_count * 1000
        + tech_count * 2000
        + product_count * 1500
        + company.level * 3000
    )
    # ±20% randomness
    factor = random.uniform(0.80, 1.20)
    return base * factor


async def do_battle(
    session: AsyncSession,
    attacker_company: Company,
    defender_company: Company,
) -> tuple[str, bool]:
    """Execute a battle. Returns (result_message, attacker_won)."""
    # Count products and techs for both
    a_products = (await session.execute(
        select(Product).where(Product.company_id == attacker_company.id)
    )).scalars().all()
    d_products = (await session.execute(
        select(Product).where(Product.company_id == defender_company.id)
    )).scalars().all()
    a_techs = (await session.execute(
        select(ResearchProgress).where(
            ResearchProgress.company_id == attacker_company.id,
            ResearchProgress.status == "completed",
        )
    )).scalars().all()
    d_techs = (await session.execute(
        select(ResearchProgress).where(
            ResearchProgress.company_id == defender_company.id,
            ResearchProgress.status == "completed",
        )
    )).scalars().all()

    a_power = _calc_battle_power(attacker_company, len(a_products), len(a_techs))
    d_power = _calc_battle_power(defender_company, len(d_products), len(d_techs))

    attacker_won = a_power >= d_power
    winner = attacker_company if attacker_won else defender_company
    loser = attacker_company if not attacker_won else defender_company

    # Calculate loot
    raw_loot = int(loser.total_funds * LOOT_RATE)
    loot = max(MIN_LOOT, min(MAX_LOOT, raw_loot))
    if loser.total_funds < loot:
        loot = max(0, loser.total_funds)

    # Transfer funds
    if loot > 0:
        taken = await add_funds(session, loser.id, -loot)
        if taken:
            await add_funds(session, winner.id, loot)
        else:
            loot = 0

    lines = [
        "⚔️ 商战结果",
        f"{'─' * 24}",
        f"🔴 {attacker_company.name}  战力: {a_power:,.0f}",
        f"🔵 {defender_company.name}  战力: {d_power:,.0f}",
        f"{'─' * 24}",
        f"🏆 胜者: {winner.name}",
    ]
    if loot > 0:
        lines.append(f"💰 掠夺: {fmt_traffic(loot)} (从 {loser.name})")
    else:
        lines.append("💸 对方资金不足，未能掠夺")

    lines.append(f"\n💬 {_pick_taunt(winner.name, loser.name)}")

    return "\n".join(lines), attacker_won


async def battle(
    session: AsyncSession,
    attacker_tg_id: int,
    defender_tg_id: int,
) -> tuple[bool, str]:
    """Full battle flow with validation. Returns (success, message)."""
    from services.user_service import get_user_by_tg_id
    from services.company_service import get_companies_by_owner

    # Cooldown check
    cd = await _check_cooldown(attacker_tg_id)
    if cd > 0:
        mins = cd // 60
        secs = cd % 60
        return False, f"⏳ 商战冷却中，还需 {mins}分{secs}秒"

    if attacker_tg_id == defender_tg_id:
        return False, "❌ 不能对自己发起商战"

    attacker_user = await get_user_by_tg_id(session, attacker_tg_id)
    defender_user = await get_user_by_tg_id(session, defender_tg_id)
    if not attacker_user:
        return False, "❌ 你还未注册，请先 /start"
    if not defender_user:
        return False, "❌ 对方还未注册"

    a_companies = await get_companies_by_owner(session, attacker_user.id)
    d_companies = await get_companies_by_owner(session, defender_user.id)
    if not a_companies:
        return False, "❌ 你还没有公司，无法发起商战"
    if not d_companies:
        return False, "❌ 对方没有公司，无法商战"

    # Use first company for both
    a_company = a_companies[0]
    d_company = d_companies[0]

    msg, _ = await do_battle(session, a_company, d_company)
    await _set_cooldown(attacker_tg_id)
    return True, msg

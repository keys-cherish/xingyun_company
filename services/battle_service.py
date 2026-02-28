"""Business battle (商战) with strategy, underdog mechanics, and bilateral damage."""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cache.redis_client import get_redis
from db.models import Company, Product, ResearchProgress, User
from services.company_service import add_funds
from utils.formatters import fmt_traffic

# Cooldown base: 1 battle per user every 30 minutes
BATTLE_COOLDOWN_SECONDS = 1800

# Base loot settings
BASE_LOOT_RATE = 0.05
MIN_LOOT = 500
MAX_LOOT = 50000

# Training mode: companies younger than this are protected
TRAINING_MODE_DAYS = 3

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
]


@dataclass(frozen=True)
class BattleStrategy:
    key: str
    name: str
    aliases: tuple[str, ...]
    power_bonus: float
    random_spread: float
    self_damage_mult: float
    loot_mult: float
    cooldown_mult: float
    underdog_bonus_mult: float


STRATEGIES: dict[str, BattleStrategy] = {
    "balanced": BattleStrategy(
        key="balanced",
        name="稳扎稳打",
        aliases=("稳扎稳打", "稳", "balanced", "default", "normal"),
        power_bonus=0.00,
        random_spread=0.12,
        self_damage_mult=1.00,
        loot_mult=1.00,
        cooldown_mult=1.00,
        underdog_bonus_mult=1.00,
    ),
    "aggressive": BattleStrategy(
        key="aggressive",
        name="激进营销",
        aliases=("激进营销", "激进", "aggressive", "aggro"),
        power_bonus=0.08,
        random_spread=0.20,
        self_damage_mult=1.35,
        loot_mult=1.15,
        cooldown_mult=1.15,
        underdog_bonus_mult=0.90,
    ),
    "ambush": BattleStrategy(
        key="ambush",
        name="奇袭渗透",
        aliases=("奇袭渗透", "奇袭", "偷袭", "ambush"),
        power_bonus=-0.02,
        random_spread=0.18,
        self_damage_mult=0.95,
        loot_mult=1.05,
        cooldown_mult=0.95,
        underdog_bonus_mult=1.25,
    ),
}
DEFAULT_STRATEGY = STRATEGIES["balanced"]
VALID_STRATEGY_HINT = "稳扎稳打 / 激进营销 / 奇袭渗透"


def _pick_taunt(winner_name: str, loser_name: str) -> str:
    return random.choice(_TAUNTS).format(winner=winner_name, loser=loser_name)


def _is_training_mode(a: Company, b: Company) -> bool:
    """Either company < TRAINING_MODE_DAYS old → training mode."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    cutoff = dt.timedelta(days=TRAINING_MODE_DAYS)
    return (now - a.created_at) < cutoff or (now - b.created_at) < cutoff


def _resolve_strategy(raw: str | None) -> BattleStrategy | None:
    if raw is None or not raw.strip():
        return DEFAULT_STRATEGY
    key = raw.strip().lower()
    for strategy in STRATEGIES.values():
        if key == strategy.key or key in strategy.aliases:
            return strategy
    return None


async def _check_cooldown(tg_id: int) -> int:
    """Return remaining cooldown seconds, 0 if ready."""
    r = await get_redis()
    ttl = await r.ttl(f"battle_cd:{tg_id}")
    return max(0, ttl)


async def _set_cooldown(tg_id: int, seconds: int):
    r = await get_redis()
    await r.set(f"battle_cd:{tg_id}", "1", ex=max(1, int(seconds)))


def _calc_base_power(company: Company, product_count: int, tech_count: int) -> float:
    """Calculate static power before strategy and random roll."""
    return max(
        1000.0,
        company.total_funds * 0.3
        + company.daily_revenue * 30
        + company.employee_count * 1000
        + tech_count * 2000
        + product_count * 1500
        + company.level * 3000,
    )


def _roll_power(base_power: float, strategy: BattleStrategy) -> float:
    spread = min(0.35, max(0.08, strategy.random_spread))
    factor = random.uniform(1.0 - spread, 1.0 + spread)
    return base_power * (1.0 + strategy.power_bonus) * factor


def _calc_underdog_multipliers(
    attacker_base: float,
    defender_base: float,
    attacker_strategy: BattleStrategy,
    defender_strategy: BattleStrategy,
) -> tuple[float, float, list[str]]:
    """Return (attacker_mult, defender_mult, hints)."""
    hints: list[str] = []
    if attacker_base <= 0 or defender_base <= 0:
        return 1.0, 1.0, hints

    if attacker_base == defender_base:
        return 1.0, 1.0, hints

    attacker_is_underdog = attacker_base < defender_base
    weak = attacker_base if attacker_is_underdog else defender_base
    strong = defender_base if attacker_is_underdog else attacker_base
    weak_strategy = attacker_strategy if attacker_is_underdog else defender_strategy

    gap = 1.0 - (weak / strong)  # 0~1
    base_bonus = min(0.45, gap * 0.90) * weak_strategy.underdog_bonus_mult

    hints.append(f"🪄 弱势补正触发：+{base_bonus * 100:.1f}%")

    attacker_mult = 1.0
    defender_mult = 1.0
    if attacker_is_underdog:
        attacker_mult *= 1.0 + base_bonus
    else:
        defender_mult *= 1.0 + base_bonus

    # Black swan makes upset possible but not common.
    black_swan_chance = min(0.35, gap * 0.60 * weak_strategy.underdog_bonus_mult)
    if random.random() < black_swan_chance:
        swan_bonus = random.uniform(0.15, 0.35)
        favorite_debuff = random.uniform(0.85, 0.95)
        hints.append("🌪 黑天鹅事件：弱势方抓住窗口，强势方出现重大失误！")

        if attacker_is_underdog:
            attacker_mult *= 1.0 + swan_bonus
            defender_mult *= favorite_debuff
        else:
            defender_mult *= 1.0 + swan_bonus
            attacker_mult *= favorite_debuff

    return attacker_mult, defender_mult, hints


def _calc_loot_scale(winner_base: float, loser_base: float, winner_strategy: BattleStrategy) -> float:
    if loser_base <= 0:
        return winner_strategy.loot_mult

    ratio = winner_base / loser_base
    if ratio > 1.0:
        # Strong beating weak: reduced plunder efficiency.
        scale = max(0.35, 1.0 - min(0.70, (ratio - 1.0) * 0.50))
    else:
        # Weak beating strong: reward upset.
        scale = min(1.80, 1.0 + min(0.80, (1.0 - ratio) * 1.20))
    return scale * winner_strategy.loot_mult


def _calc_cooldown_seconds(
    attacker_base: float,
    defender_base: float,
    attacker_won: bool,
    attacker_strategy: BattleStrategy,
) -> int:
    ratio = attacker_base / max(1.0, defender_base)
    cooldown = float(BATTLE_COOLDOWN_SECONDS)

    if attacker_won and ratio > 1.0:
        cooldown *= 1.0 + min(0.80, (ratio - 1.0) * 0.45)
    elif attacker_won and ratio < 1.0:
        cooldown *= 1.0 - min(0.40, (1.0 - ratio) * 0.60)
    elif (not attacker_won) and ratio > 1.3:
        cooldown *= 1.15
    elif (not attacker_won) and ratio < 0.8:
        cooldown *= 0.90

    cooldown *= attacker_strategy.cooldown_mult
    return int(max(600, min(5400, round(cooldown))))


def _calc_battle_damage(
    company: Company,
    *,
    is_winner: bool,
    intensity: float,
    strategy: BattleStrategy,
) -> tuple[int, int, int]:
    # Winner still takes visible damage, loser takes heavier damage.
    fund_rate = 0.010 if is_winner else 0.028
    emp_rate = 0.008 if is_winner else 0.030
    rep_base = 1.2 if is_winner else 3.0

    damage_mult = max(0.60, min(1.80, intensity * strategy.self_damage_mult))

    funds_loss = int(company.total_funds * fund_rate * damage_mult)
    funds_loss = max(200 if is_winner else 500, funds_loss)
    funds_loss = min(80_000 if is_winner else 150_000, funds_loss)

    employee_loss = int(round(company.employee_count * emp_rate * damage_mult))
    if company.employee_count > 1:
        employee_loss = max(1 if is_winner else 2, employee_loss)
        employee_loss = min(employee_loss, company.employee_count - 1)
    else:
        employee_loss = 0

    reputation_loss = int(round(rep_base * damage_mult + random.uniform(0.0, 1.5)))
    reputation_loss = max(1 if is_winner else 2, reputation_loss)

    return funds_loss, employee_loss, reputation_loss


async def _apply_reputation_loss(session: AsyncSession, user_id: int, loss: int) -> int:
    if loss <= 0:
        return 0
    user = await session.get(User, user_id)
    if user is None:
        return 0
    before = max(0, user.reputation)
    user.reputation = max(0, before - loss)
    await session.flush()
    return before - user.reputation


async def _apply_company_damage(
    session: AsyncSession,
    company: Company,
    *,
    funds_loss: int,
    employee_loss: int,
) -> tuple[int, int]:
    actual_funds_loss = 0
    if funds_loss > 0 and company.total_funds > 0:
        deduct = min(funds_loss, company.total_funds)
        ok = await add_funds(session, company.id, -deduct)
        if ok:
            actual_funds_loss = deduct

    actual_emp_loss = 0
    if employee_loss > 0 and company.employee_count > 1:
        actual_emp_loss = min(employee_loss, company.employee_count - 1)
        company.employee_count -= actual_emp_loss
        await session.flush()

    return actual_funds_loss, actual_emp_loss


async def do_battle(
    session: AsyncSession,
    attacker_company: Company,
    defender_company: Company,
    attacker_strategy: BattleStrategy,
) -> tuple[str, bool, int]:
    """Execute a battle. Returns (result_message, attacker_won, cooldown_seconds)."""
    defender_strategy = DEFAULT_STRATEGY

    # Count products and techs for both sides.
    a_products = (
        await session.execute(select(Product).where(Product.company_id == attacker_company.id))
    ).scalars().all()
    d_products = (
        await session.execute(select(Product).where(Product.company_id == defender_company.id))
    ).scalars().all()
    a_techs = (
        await session.execute(
            select(ResearchProgress).where(
                ResearchProgress.company_id == attacker_company.id,
                ResearchProgress.status == "completed",
            )
        )
    ).scalars().all()
    d_techs = (
        await session.execute(
            select(ResearchProgress).where(
                ResearchProgress.company_id == defender_company.id,
                ResearchProgress.status == "completed",
            )
        )
    ).scalars().all()

    attacker_base = _calc_base_power(attacker_company, len(a_products), len(a_techs))
    defender_base = _calc_base_power(defender_company, len(d_products), len(d_techs))

    a_weak_mult, d_weak_mult, underdog_hints = _calc_underdog_multipliers(
        attacker_base, defender_base, attacker_strategy, defender_strategy
    )
    attacker_power = _roll_power(attacker_base, attacker_strategy) * a_weak_mult
    defender_power = _roll_power(defender_base, defender_strategy) * d_weak_mult

    attacker_won = attacker_power >= defender_power
    winner = attacker_company if attacker_won else defender_company
    loser = defender_company if attacker_won else attacker_company
    winner_strategy = attacker_strategy if attacker_won else defender_strategy

    # Similar power -> higher intensity and heavier battle damage.
    power_gap = abs(attacker_power - defender_power) / max(attacker_power, defender_power)
    intensity = 1.30 - min(0.75, power_gap)

    training = _is_training_mode(attacker_company, defender_company)

    if training:
        # TRAINING MODE: no damage, half loot from system (loser pays nothing)
        winner_base = attacker_base if attacker_won else defender_base
        loser_base = defender_base if attacker_won else attacker_base
        loot_scale = _calc_loot_scale(winner_base, loser_base, winner_strategy)
        raw_loot = int(loser.total_funds * BASE_LOOT_RATE * loot_scale)
        loot = max(MIN_LOOT // 2, min(MAX_LOOT // 2, raw_loot // 2))
        if loot > 0:
            await add_funds(session, winner.id, loot)

        cooldown_seconds = _calc_cooldown_seconds(
            attacker_base, defender_base, attacker_won, attacker_strategy
        )
        mins = cooldown_seconds // 60
        secs = cooldown_seconds % 60

        lines = [
            "🎓 训练赛商战",
            f"{'─' * 24}",
            f"🟥 {attacker_company.name}（{attacker_strategy.name}） 战力: {attacker_power:,.0f}",
            f"🟦 {defender_company.name}（{defender_strategy.name}） 战力: {defender_power:,.0f}",
        ]
        lines.extend(underdog_hints)
        lines += [
            f"{'─' * 24}",
            f"🏆 胜者: {winner.name}",
            f"💰 训练奖金: {fmt_traffic(loot)}（系统发放，败者无损失）",
            "",
            f"🎓 公司成立不满{TRAINING_MODE_DAYS}天，自动进入训练模式:",
            "  • 胜者获半额奖金（系统发放）",
            "  • 败者零损失（资金/员工/声望不变）",
            f"⏳ 下次商战冷却: {mins}分{secs}秒",
            "",
            f"💬 {_pick_taunt(winner.name, loser.name)}",
        ]
        return "\n".join(lines), attacker_won, cooldown_seconds

    # ---- NORMAL MODE: bilateral damage + loot ----
    a_fund_loss_raw, a_emp_loss_raw, a_rep_loss_raw = _calc_battle_damage(
        attacker_company, is_winner=attacker_won, intensity=intensity, strategy=attacker_strategy
    )
    d_fund_loss_raw, d_emp_loss_raw, d_rep_loss_raw = _calc_battle_damage(
        defender_company, is_winner=not attacker_won, intensity=intensity, strategy=defender_strategy
    )

    a_fund_loss, a_emp_loss = await _apply_company_damage(
        session, attacker_company, funds_loss=a_fund_loss_raw, employee_loss=a_emp_loss_raw
    )
    d_fund_loss, d_emp_loss = await _apply_company_damage(
        session, defender_company, funds_loss=d_fund_loss_raw, employee_loss=d_emp_loss_raw
    )
    a_rep_loss = await _apply_reputation_loss(session, attacker_company.owner_id, a_rep_loss_raw)
    d_rep_loss = await _apply_reputation_loss(session, defender_company.owner_id, d_rep_loss_raw)

    # Loot transfer after battle damage.
    winner_base = attacker_base if attacker_won else defender_base
    loser_base = defender_base if attacker_won else attacker_base
    loot_scale = _calc_loot_scale(winner_base, loser_base, winner_strategy)
    raw_loot = int(loser.total_funds * BASE_LOOT_RATE * loot_scale)
    loot = max(MIN_LOOT, min(MAX_LOOT, raw_loot))
    if loser.total_funds < loot:
        loot = max(0, loser.total_funds)

    if loot > 0:
        taken = await add_funds(session, loser.id, -loot)
        if taken:
            await add_funds(session, winner.id, loot)
        else:
            loot = 0

    cooldown_seconds = _calc_cooldown_seconds(
        attacker_base, defender_base, attacker_won, attacker_strategy
    )
    mins = cooldown_seconds // 60
    secs = cooldown_seconds % 60

    lines = [
        "⚔️ 商战结果",
        "─" * 24,
        f"🟥 {attacker_company.name}（{attacker_strategy.name}） 战力: {attacker_power:,.0f}",
        f"🟦 {defender_company.name}（{defender_strategy.name}） 战力: {defender_power:,.0f}",
    ]
    lines.extend(underdog_hints)
    lines += [
        "─" * 24,
        f"🏆 胜者: {winner.name}",
    ]

    if loot > 0:
        lines.append(f"💰 掠夺: {fmt_traffic(loot)} (从 {loser.name})")
    else:
        lines.append("💸 对方资金不足，未能掠夺")

    lines += [
        "",
        "🩸 双边战损",
        f"• {attacker_company.name}: 资金-{fmt_traffic(a_fund_loss)} | 员工-{a_emp_loss} | 声望-{a_rep_loss}",
        f"• {defender_company.name}: 资金-{fmt_traffic(d_fund_loss)} | 员工-{d_emp_loss} | 声望-{d_rep_loss}",
        f"⏳ 你的下次商战冷却: {mins}分{secs}秒",
        "",
        f"💬 {_pick_taunt(winner.name, loser.name)}",
    ]
    return "\n".join(lines), attacker_won, cooldown_seconds


async def battle(
    session: AsyncSession,
    attacker_tg_id: int,
    defender_tg_id: int,
    attacker_strategy: str | None = None,
) -> tuple[bool, str]:
    """Full battle flow with validation. Returns (success, message)."""
    from services.company_service import get_companies_by_owner
    from services.user_service import get_user_by_tg_id

    strategy = _resolve_strategy(attacker_strategy)
    if strategy is None:
        return False, f"❌ 无效战术: {attacker_strategy}\n可选: {VALID_STRATEGY_HINT}"

    # Cooldown check.
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

    # Use first company for both.
    a_company = a_companies[0]
    d_company = d_companies[0]

    msg, _attacker_won, cooldown_seconds = await do_battle(
        session, a_company, d_company, strategy
    )
    await _set_cooldown(attacker_tg_id, cooldown_seconds)

    # Quest progress: winner gets battle_win_count +1
    from services.quest_service import update_quest_progress
    winner_owner = attacker_user.id if _attacker_won else defender_user.id
    await update_quest_progress(session, winner_owner, "battle_win_count", increment=1)

    return True, msg

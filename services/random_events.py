"""Random events system - adds unpredictability and fun to the game.

Events can trigger during daily settlement or be checked periodically.
Types: employee resignation, retirement, sick leave, market boom, PR crisis, etc.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Company, Product
from services.company_service import add_funds
from services.user_service import add_points, add_reputation


@dataclass
class GameEvent:
    name: str
    description: str
    category: str  # employee / market / pr / lucky
    effect_type: str  # income_pct / flat_traffic / reputation / product_quality / employee
    effect_value: float  # positive = good, negative = bad
    weight: int  # probability weight


EVENTS: list[GameEvent] = [
    # Employee events
    GameEvent("核心员工离职", "一名核心员工突然离职，人员减少", "employee", "employee", -1, 12),
    GameEvent("员工退休", "一位资深员工到了退休年龄", "employee", "employee", -1, 8),
    GameEvent("员工请假潮", "季节性请假，团队效率下降", "employee", "income_pct", -0.03, 20),
    GameEvent("招到优秀人才", "从竞争对手挖到了一名高级工程师", "employee", "employee", 1, 10),
    GameEvent("团队建设成功", "团建效果显著，团队凝聚力提升", "employee", "income_pct", 0.05, 15),
    GameEvent("员工获奖", "公司员工在技术比赛中获奖，提升声望", "employee", "reputation", 5, 8),
    GameEvent("集体病假", "流感季节，多名员工请假", "employee", "income_pct", -0.08, 6),
    GameEvent("员工生育假", "有员工进入产假/陪产假", "employee", "income_pct", -0.02, 8),

    # Market events
    GameEvent("行业利好", "政策扶持，行业迎来增长", "market", "income_pct", 0.15, 8),
    GameEvent("市场低迷", "经济下行，市场需求萎缩", "market", "income_pct", -0.12, 8),
    GameEvent("竞品暴雷", "主要竞争对手出了大问题，客户涌入", "market", "flat_traffic", 1000, 5),
    GameEvent("供应链中断", "上游供应链出现问题，运营成本增加", "market", "flat_traffic", -500, 10),

    # PR events
    GameEvent("媒体正面报道", "知名媒体发布了关于公司的正面文章", "pr", "reputation", 8, 10),
    GameEvent("公关危机", "负面舆情发酵，声望受损", "pr", "reputation", -5, 8),
    GameEvent("CEO演讲走红", "公司CEO的演讲视频意外走红", "pr", "reputation", 12, 5),

    # Lucky events
    GameEvent("天降横财", "意外收到一笔投资", "lucky", "flat_traffic", 2000, 3),
    GameEvent("中了行业大奖", "公司产品获得年度行业大奖", "lucky", "reputation", 20, 2),
    GameEvent("服务器故障", "服务器出现严重故障，紧急修复花费不少", "lucky", "flat_traffic", -800, 7),

    # Product events
    GameEvent("产品好评如潮", "用户反馈极好，产品口碑传播", "market", "product_quality", 3, 10),
    GameEvent("产品出现Bug", "线上出现严重Bug，紧急修复中", "market", "product_quality", -2, 12),
]

# Chance that any event fires during settlement (per company)
EVENT_CHANCE = 0.35  # 35% chance per company per day


async def roll_daily_events(session: AsyncSession, company: Company) -> list[str]:
    """Roll for random events during daily settlement. Returns event descriptions."""
    messages = []

    if random.random() > EVENT_CHANCE:
        return messages  # No event today

    # Roll 1-2 events
    num_events = random.choices([1, 2], weights=[75, 25], k=1)[0]
    weights = [e.weight for e in EVENTS]
    selected = random.choices(EVENTS, weights=weights, k=num_events)
    # Deduplicate by name
    seen = set()
    unique = []
    for e in selected:
        if e.name not in seen:
            seen.add(e.name)
            unique.append(e)

    for event in unique:
        msg = await _apply_event(session, company, event)
        messages.append(msg)

    return messages


async def _apply_event(session: AsyncSession, company: Company, event: GameEvent) -> str:
    """Apply a single event and return a description string."""
    effect_desc = ""

    if event.effect_type == "income_pct":
        # Adjust daily_revenue temporarily (applied as bonus/penalty in settlement)
        change = int(company.daily_revenue * event.effect_value)
        await add_funds(session, company.id, change)
        sign = "+" if change >= 0 else ""
        effect_desc = f"资金变动: {sign}{change}"

    elif event.effect_type == "flat_traffic":
        amount = int(event.effect_value)
        if amount > 0:
            await add_funds(session, company.id, amount)
            effect_desc = f"资金+{amount}"
        else:
            await add_funds(session, company.id, amount)
            effect_desc = f"资金{amount}"

    elif event.effect_type == "reputation":
        rep = int(event.effect_value)
        await add_reputation(session, company.owner_id, max(rep, 0))
        sign = "+" if rep >= 0 else ""
        effect_desc = f"声望{sign}{rep}"

    elif event.effect_type == "employee":
        change = int(event.effect_value)
        new_count = max(1, company.employee_count + change)
        from config import settings as cfg
        max_emp = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1)
        new_count = min(new_count, max_emp)
        company.employee_count = new_count
        await session.flush()
        sign = "+" if change > 0 else ""
        effect_desc = f"员工变动: {sign}{change} (当前: {new_count}人)"

    elif event.effect_type == "product_quality":
        # Adjust quality of a random product
        result = await session.execute(
            select(Product).where(Product.company_id == company.id)
        )
        products = list(result.scalars().all())
        if products:
            target = random.choice(products)
            target.quality = max(1, target.quality + int(event.effect_value))
            await session.flush()
            effect_desc = f"产品「{target.name}」品质变动: {'+' if event.effect_value > 0 else ''}{int(event.effect_value)}"
        else:
            effect_desc = "无产品受影响"

    # Award points for experiencing events (even bad ones are "content")
    await add_points(company.owner_id, 1)

    category_emoji = {"employee": "👤", "market": "📊", "pr": "📰", "lucky": "🎲"}
    emoji = category_emoji.get(event.category, "❓")
    return f"{emoji} 【{event.name}】{event.description}\n   → {effect_desc}"

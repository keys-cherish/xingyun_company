"""全面商战 — @mention trigger + confirmation + sequential battles."""

from __future__ import annotations

import re
import logging

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from keyboards.menus import tag_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()
logger = logging.getLogger(__name__)

# War trigger keywords — must contain at least one from each group
_WAR_KEYWORDS = ("商战", "宣战", "开战", "打仗", "进攻", "出征")
_ALL_KEYWORDS = ("所有", "全部", "all", "全面", "不计成本", "不择手段", "不遗余力")

# Costs
WAR_POINT_COST = 1000
WAR_FUND_RATE = 0.05  # 5% of company funds
WAR_COOLDOWN_SECONDS = 4 * 3600  # 4 hours
WAR_SELF_REVENUE_BUFF_RATE = 0.15  # +15% revenue buff for going all-in


def _has_war_intent(text: str) -> bool:
    """Check if message text contains war + all-target intent."""
    has_war = any(kw in text for kw in _WAR_KEYWORDS)
    has_all = any(kw in text for kw in _ALL_KEYWORDS)
    return has_war and has_all


@router.message(
    F.text
    & ~F.text.startswith("/")
    & (
        F.text.contains("商战")
        | F.text.contains("宣战")
        | F.text.contains("开战")
        | F.text.contains("打仗")
        | F.text.contains("进攻")
        | F.text.contains("出征")
    )
    & (
        F.text.contains("所有")
        | F.text.contains("全部")
        | F.text.contains("all")
        | F.text.contains("全面")
        | F.text.contains("不计成本")
        | F.text.contains("不择手段")
        | F.text.contains("不遗余力")
    )
)
async def on_total_war_mention(message: types.Message):
    """Detect @bot mention + war keywords → show confirmation panel."""
    if not message.from_user or message.from_user.is_bot:
        return

    text = (message.text or "").strip()
    if not text:
        return

    # Check for @bot mention
    bot_user = await message.bot.get_me()
    bot_username = (bot_user.username or "").strip()
    if not bot_username:
        return

    username = re.escape(bot_username)
    mention_pattern = rf"(?<![A-Za-z0-9_])@{username}(?![A-Za-z0-9_])"
    if not re.search(mention_pattern, text, flags=re.IGNORECASE):
        return

    # Check for war intent
    if not _has_war_intent(text):
        return  # Let AI chat handler deal with it

    tg_id = message.from_user.id

    # Load user + company
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await message.reply("请先 /company_create 创建公司")
            return
        companies = await get_companies_by_owner(session, user.id)
        if not companies:
            await message.reply("你还没有公司，无法发起全面商战")
            return
        my_company = companies[0]

        # Check cooldown
        from cache.redis_client import get_redis
        r = await get_redis()
        cd_ttl = await r.ttl(f"totalwar_cd:{tg_id}")
        if cd_ttl > 0:
            mins = cd_ttl // 60
            await message.reply(f"⏳ 全面商战冷却中，还需 {mins} 分钟")
            return

        # Load all other companies for comparison
        from sqlalchemy import select
        from db.models import Company, Product, ResearchProgress
        from sqlalchemy import func as sqlfunc
        from services.battle_service import _calc_base_power

        result = await session.execute(select(Company).where(Company.id != my_company.id))
        targets = list(result.scalars().all())

        if not targets:
            await message.reply("没有其他公司可以发起商战")
            return

        # Calc my power
        my_prods = (await session.execute(
            select(sqlfunc.count()).where(Product.company_id == my_company.id)
        )).scalar() or 0
        my_techs = (await session.execute(
            select(sqlfunc.count()).where(
                ResearchProgress.company_id == my_company.id,
                ResearchProgress.status == "completed",
            )
        )).scalar() or 0
        my_power = _calc_base_power(my_company, my_prods, my_techs)

        # Calc target powers
        target_info = []
        total_target_power = 0
        for t in targets:
            t_prods = (await session.execute(
                select(sqlfunc.count()).where(Product.company_id == t.id)
            )).scalar() or 0
            t_techs = (await session.execute(
                select(sqlfunc.count()).where(
                    ResearchProgress.company_id == t.id,
                    ResearchProgress.status == "completed",
                )
            )).scalar() or 0
            t_power = _calc_base_power(t, t_prods, t_techs)
            total_target_power += t_power
            ratio = my_power / max(1, t_power)
            if ratio > 1.5:
                outlook = "🟢 碾压"
            elif ratio > 1.0:
                outlook = "🟡 优势"
            elif ratio > 0.7:
                outlook = "🟠 劣势"
            else:
                outlook = "🔴 危险"
            target_info.append((t, t_power, outlook))

        # Sort by power descending
        target_info.sort(key=lambda x: x[1], reverse=True)

    # Calculate costs
    fund_cost = int(my_company.total_funds * WAR_FUND_RATE)
    fund_cost = max(2000, fund_cost)  # minimum 2000

    # Check points
    from services.user_service import add_points
    from cache.redis_client import get_redis
    r = await get_redis()
    current_points = int(await r.get(f"points:{tg_id}") or 0)

    # Build warning panel
    lines = [
        "⚔️🔥 全面商战 — 终极宣战 🔥⚔️",
        f"{'─' * 28}",
        f"🏢 {my_company.name}  战力: {my_power:,.0f}",
        f"💰 资金: {fmt_traffic(my_company.total_funds)}",
        f"👷 员工: {my_company.employee_count}人",
        f"{'─' * 28}",
        f"🎯 宣战对象: {len(targets)} 家公司",
        "",
    ]

    # Show top targets (max 10 to keep message short)
    for i, (t, t_power, outlook) in enumerate(target_info[:10]):
        lines.append(f"  {i+1}. {t.name} — 战力 {t_power:,.0f} {outlook}")
    if len(target_info) > 10:
        lines.append(f"  ...还有 {len(target_info) - 10} 家")

    lines.extend([
        "",
        f"{'─' * 28}",
        "💸 全面商战代价:",
        f"  🏅 积分消耗: {WAR_POINT_COST}（当前: {current_points}）",
        f"  💰 资金消耗: {fmt_traffic(fund_cost)}（公司资金的{int(WAR_FUND_RATE*100)}%）",
        f"  👷 预计员工损失: 3-8%",
        f"  ⭐ 预计声望损失: 每战 1-5",
        "",
        "🎁 胜利收益:",
        f"  📈 全面商战Buff: 营收+{int(WAR_SELF_REVENUE_BUFF_RATE*100)}%（至次日结算）",
        f"  💰 每胜一场: 掠夺对方资金",
        f"  🧨 每胜一场: 对方营收Debuff -12%",
        "",
        "⚠️ 警告:",
        "  • 败给任何一家将导致自己营收Debuff",
        "  • 败方额外道德 -8",
        "  • 全面商战冷却 4 小时",
        f"{'─' * 28}",
    ])

    can_afford = current_points >= WAR_POINT_COST and my_company.total_funds >= fund_cost
    if not can_afford:
        if current_points < WAR_POINT_COST:
            lines.append(f"❌ 积分不足！需要 {WAR_POINT_COST}，当前 {current_points}")
        if my_company.total_funds < fund_cost:
            lines.append(f"❌ 资金不足！需要 {fmt_traffic(fund_cost)}")

    buttons = []
    if can_afford:
        buttons.append([
            InlineKeyboardButton(
                text=f"⚔️ 确认全面宣战（{WAR_POINT_COST}积分 + {fmt_traffic(fund_cost)}）",
                callback_data=f"totalwar:confirm:{my_company.id}",
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="🔙 取消", callback_data=f"company:view:{my_company.id}"),
    ])

    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)
    sent = await message.reply("\n".join(lines), reply_markup=kb)

    from utils.panel_owner import mark_panel
    await mark_panel(message.chat.id, sent.message_id, tg_id)


@router.callback_query(F.data.startswith("totalwar:confirm:"))
async def cb_total_war_confirm(callback: types.CallbackQuery):
    """Execute total war after confirmation."""
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    # Show "executing" message immediately
    await callback.answer("⚔️ 全面商战开始！请稍候...", show_alert=True)

    try:
        async with async_session() as session:
            async with session.begin():
                user = await get_user_by_tg_id(session, tg_id)
                if not user:
                    await callback.message.edit_text("❌ 用户不存在")
                    return
                my_company = await get_company_by_id(session, company_id)
                if not my_company or my_company.owner_id != user.id:
                    await callback.message.edit_text("❌ 无权操作")
                    return

                # Re-check cooldown
                from cache.redis_client import get_redis
                r = await get_redis()
                cd_ttl = await r.ttl(f"totalwar_cd:{tg_id}")
                if cd_ttl > 0:
                    await callback.message.edit_text(f"⏳ 冷却中，还需 {cd_ttl // 60} 分钟")
                    return

                # Consume points
                lua = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', key) or '0')
if current < amount then
    return 0
end
redis.call('DECRBY', key, amount)
return 1
"""
                ok = await r.eval(lua, 1, f"points:{tg_id}", WAR_POINT_COST)
                if int(ok) != 1:
                    await callback.message.edit_text(f"❌ 积分不足，需要 {WAR_POINT_COST}")
                    return

                # Consume company funds
                fund_cost = max(2000, int(my_company.total_funds * WAR_FUND_RATE))
                from services.company_service import add_funds
                fund_ok = await add_funds(session, company_id, -fund_cost)
                if not fund_ok:
                    # Refund points
                    await r.incrby(f"points:{tg_id}", WAR_POINT_COST)
                    await callback.message.edit_text("❌ 公司资金不足")
                    return

                # Load all targets
                from sqlalchemy import select
                from db.models import Company
                result = await session.execute(select(Company).where(Company.id != company_id))
                targets = list(result.scalars().all())

                if not targets:
                    # Refund
                    await add_funds(session, company_id, fund_cost)
                    await r.incrby(f"points:{tg_id}", WAR_POINT_COST)
                    await callback.message.edit_text("❌ 没有可宣战的公司")
                    return

                # Execute battles
                from services.battle_service import do_battle, STRATEGIES

                aggressive = STRATEGIES["aggressive"]
                wins = 0
                losses = 0
                total_loot = 0
                battle_lines = []

                for target in targets:
                    try:
                        msg, attacker_won, _ = await do_battle(
                            session, my_company, target, aggressive
                        )
                        if attacker_won:
                            wins += 1
                            # Extract loot from message (rough parse)
                            battle_lines.append(f"  ✅ vs {target.name} — 胜")
                        else:
                            losses += 1
                            battle_lines.append(f"  ❌ vs {target.name} — 败")
                        # Refresh company object after each battle
                        await session.refresh(my_company)
                    except Exception as e:
                        logger.warning("Total war battle error vs %s: %s", target.name, e)
                        battle_lines.append(f"  ⚠️ vs {target.name} — 异常")

                # Apply self revenue buff if any wins
                if wins > 0:
                    from services.battle_service import _next_settlement_time
                    import datetime as dt
                    ttl = int(max(60, (
                        _next_settlement_time() - dt.datetime.now(dt.UTC).replace(tzinfo=None)
                    ).total_seconds()))
                    buff_rate = WAR_SELF_REVENUE_BUFF_RATE
                    await r.set(f"totalwar_buff:{company_id}", f"{buff_rate:.4f}", ex=ttl)

                # Set cooldown
                await r.set(f"totalwar_cd:{tg_id}", "1", ex=WAR_COOLDOWN_SECONDS)

        # Build result message
        win_rate = wins / max(1, wins + losses) * 100
        result_lines = [
            "⚔️🔥 全面商战结果 🔥⚔️",
            f"{'─' * 28}",
            f"🏢 {my_company.name}",
            f"🎯 宣战 {len(targets)} 家公司",
            f"{'─' * 28}",
            f"✅ 胜: {wins} 场 | ❌ 败: {losses} 场 | 胜率: {win_rate:.0f}%",
            f"{'─' * 28}",
        ]
        result_lines.extend(battle_lines[:15])
        if len(battle_lines) > 15:
            result_lines.append(f"  ...还有 {len(battle_lines) - 15} 场")

        result_lines.extend([
            f"{'─' * 28}",
            f"💸 总消耗: {WAR_POINT_COST}积分 + {fmt_traffic(fund_cost)}资金",
        ])
        if wins > 0:
            result_lines.append(
                f"📈 全面商战Buff: 营收+{int(WAR_SELF_REVENUE_BUFF_RATE*100)}%（至次日结算）"
            )
        result_lines.append(f"⏳ 下次全面商战冷却: {WAR_COOLDOWN_SECONDS // 3600}小时")

        if win_rate >= 80:
            result_lines.append("\n🔥 压倒性胜利！你的商业帝国威震四方！")
        elif win_rate >= 50:
            result_lines.append("\n⚔️ 虽有胜负，但总体占优！实力不俗！")
        elif wins > 0:
            result_lines.append("\n💪 虽败多胜少，但勇气可嘉！下次准备更充分再来！")
        else:
            result_lines.append("\n😵 全军覆没...建议先壮大实力再发动全面商战。")

        kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回公司", callback_data=f"company:view:{company_id}")],
        ]), tg_id)
        await callback.message.edit_text("\n".join(result_lines), reply_markup=kb)

    except Exception as e:
        logger.exception("Total war error")
        try:
            await callback.message.edit_text(f"❌ 全面商战出错: {e}")
        except Exception:
            pass

"""🎰 老虎机游戏 — 每日奖励仅一次，可重复游玩。"""

from __future__ import annotations

import logging
import random

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from keyboards.menus import tag_kb
from services.company_service import add_funds, get_companies_by_owner
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic
from utils.panel_owner import mark_panel

router = Router()
logger = logging.getLogger(__name__)

CMD_SLOT = "cp_slot"

# ── 老虎机符号与权重 ──────────────────────────────────
SYMBOLS = [
    ("🍒", 15),   # 樱桃 — 最常见
    ("🍋", 14),   # 柠檬
    ("🍊", 13),   # 橘子
    ("🍇", 12),   # 葡萄
    ("🔔", 8),    # 铃铛
    ("💎", 5),    # 钻石 — 稀有
    ("7️⃣", 3),   # 7 — 最稀有
]

# 三个一样时的奖金表
REWARD_TABLE: dict[str, int] = {
    "🍒": 500,
    "🍋": 800,
    "🍊": 1200,
    "🍇": 2000,
    "🔔": 5000,
    "💎": 20000,
    "7️⃣": 77777,
}

_SYMBOL_LIST = [s for s, _ in SYMBOLS]
_WEIGHTS = [w for _, w in SYMBOLS]

# Redis key: slot_reward:{tg_id}  — 当日是否已领取奖励
_REDIS_KEY = "slot_reward:{tg_id}"
_REDIS_TTL_SECONDS = 86400  # 24h


def _spin() -> list[str]:
    """随机摇三个符号。"""
    return random.choices(_SYMBOL_LIST, weights=_WEIGHTS, k=3)


def _format_reels(reels: list[str]) -> str:
    """格式化老虎机显示。"""
    return (
        f"┌───┬───┬───┐\n"
        f"│ {reels[0]} │ {reels[1]} │ {reels[2]} │\n"
        f"└───┬───┬───┘"
    )


async def _check_daily_rewarded(tg_id: int) -> bool:
    """检查今天是否已领取过奖励。"""
    from cache.redis_client import get_redis
    r = await get_redis()
    return bool(await r.exists(_REDIS_KEY.format(tg_id=tg_id)))


async def _mark_daily_rewarded(tg_id: int):
    """标记今天已领取奖励。"""
    from cache.redis_client import get_redis
    r = await get_redis()
    import datetime as dt
    # TTL 到当日北京时间 00:00
    from utils.timezone import BJ_TZ
    now_bj = dt.datetime.now(BJ_TZ)
    next_midnight = (now_bj + dt.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    ttl = int((next_midnight - now_bj).total_seconds())
    ttl = max(ttl, 60)  # 至少 60 秒
    await r.set(_REDIS_KEY.format(tg_id=tg_id), "1", ex=ttl)


async def _do_spin(tg_id: int) -> str:
    """执行一次老虎机，返回展示文本。"""
    reels = _spin()
    display = _format_reels(reels)

    # 判断是否中奖
    if reels[0] == reels[1] == reels[2]:
        symbol = reels[0]
        reward = REWARD_TABLE.get(symbol, 500)

        # 检查今日是否已领奖
        already_rewarded = await _check_daily_rewarded(tg_id)
        if already_rewarded:
            return (
                f"🎰 老虎机\n{display}\n\n"
                f"🎉 三个{symbol}！本可获得 {fmt_traffic(reward)}！\n"
                f"但你今天已经领过奖励了～明天再来吧"
            )

        # 发放奖励
        async with async_session() as session:
            async with session.begin():
                user = await get_user_by_tg_id(session, tg_id)
                if not user:
                    return (
                        f"🎰 老虎机\n{display}\n\n"
                        f"🎉 三个{symbol}！但你还没注册，奖励无法发放"
                    )
                companies = await get_companies_by_owner(session, user.id)
                if not companies:
                    return (
                        f"🎰 老虎机\n{display}\n\n"
                        f"🎉 三个{symbol}！但你还没有公司，奖励无法发放"
                    )
                company = companies[0]
                await add_funds(session, company.id, reward)
                company_name = company.name

        await _mark_daily_rewarded(tg_id)

        jackpot_msg = ""
        if symbol == "7️⃣":
            jackpot_msg = "\n\n🏆🏆🏆 JACKPOT! 777大奖！🏆🏆🏆"

        return (
            f"🎰 老虎机\n{display}\n\n"
            f"🎉 三个{symbol}！恭喜中奖！{jackpot_msg}\n"
            f"💰 奖金 {fmt_traffic(reward)} 已存入「{company_name}」"
        )

    # 两个相同 — 差一点
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return f"🎰 老虎机\n{display}\n\n😮 差一点就中了！再来一次？"

    return f"🎰 老虎机\n{display}\n\n💨 没中奖，再试试手气？"


# ── 命令入口 ──────────────────────────────────────────

@router.message(Command(CMD_SLOT))
async def cmd_slot(message: types.Message):
    """老虎机命令入口。"""
    tg_id = message.from_user.id
    result_text = await _do_spin(tg_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 再来一次!", callback_data="slot:spin")],
    ])
    sent = await message.answer(result_text, reply_markup=tag_kb(kb, tg_id))
    await mark_panel(sent.chat.id, sent.message_id, tg_id)


@router.callback_query(F.data == "slot:spin")
async def cb_slot_spin(callback: types.CallbackQuery):
    """老虎机按钮再来一次。"""
    tg_id = callback.from_user.id
    result_text = await _do_spin(tg_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 再来一次!", callback_data="slot:spin")],
    ])
    try:
        await callback.message.edit_text(result_text, reply_markup=tag_kb(kb, tg_id))
    except Exception:
        sent = await callback.message.answer(result_text, reply_markup=tag_kb(kb, tg_id))
        await mark_panel(sent.chat.id, sent.message_id, tg_id)
    await callback.answer()

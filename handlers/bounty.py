"""悬赏令 handler — 花声望悬赏其他公司。"""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from keyboards.menus import tag_kb
from services.bounty_service import (
    BOUNTY_ATTACKS,
    BOUNTY_LOOT_BONUS,
    BOUNTY_POWER_BONUS,
    BOUNTY_REPUTATION_COST,
    get_active_bounty,
    post_bounty,
)
from utils.panel_owner import mark_panel

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("bounty:menu:"))
async def cb_bounty_menu(callback: types.CallbackQuery):
    """Show bounty menu for a company."""
    parts = callback.data.split(":")
    company_id = int(parts[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        from db.models import Company
        from sqlalchemy import select

        # Get companies owned by others (potential targets)
        my_company = await session.get(Company, company_id)
        if not my_company:
            await callback.answer("公司不存在", show_alert=True)
            return

        result = await session.execute(
            select(Company).where(Company.id != company_id).limit(10)
        )
        targets = list(result.scalars().all())

    if not targets:
        await callback.answer("暂无可悬赏的目标", show_alert=True)
        return

    lines = [
        "🎯 悬赏令",
        f"{'─' * 24}",
        f"消耗：{BOUNTY_REPUTATION_COST} 声望",
        f"效果：攻击者战力+{int(BOUNTY_POWER_BONUS * 100)}% | 掠夺+{int(BOUNTY_LOOT_BONUS * 100)}%",
        f"有效攻击次数：{BOUNTY_ATTACKS}次 | 有效期：24小时",
        f"{'─' * 24}",
        "选择悬赏目标：",
    ]

    buttons = []
    for t in targets:
        bounty = await get_active_bounty(t.id)
        status = " 🔴已悬赏" if bounty else ""
        buttons.append([InlineKeyboardButton(
            text=f"{t.name}{status}",
            callback_data=f"bounty:confirm:{company_id}:{t.id}",
        )])

    buttons.append([InlineKeyboardButton(
        text="🔙 返回公司",
        callback_data=f"company:view:{company_id}",
    )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    try:
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=tag_kb(kb, tg_id),
        )
    except Exception:
        sent = await callback.message.answer(
            "\n".join(lines),
            reply_markup=tag_kb(kb, tg_id),
        )
        await mark_panel(sent.chat.id, sent.message_id, tg_id)
    await callback.answer()


@router.callback_query(F.data.startswith("bounty:confirm:"))
async def cb_bounty_confirm(callback: types.CallbackQuery):
    """Confirm and post a bounty."""
    parts = callback.data.split(":")
    poster_company_id = int(parts[2])
    target_company_id = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            ok, msg = await post_bounty(
                session,
                poster_tg_id=tg_id,
                poster_company_id=poster_company_id,
                target_company_id=target_company_id,
            )

    if ok:
        # Broadcast to group
        try:
            await callback.message.edit_text(msg)
        except Exception:
            await callback.message.answer(msg)
        await callback.answer("悬赏令已发布！", show_alert=True)
    else:
        await callback.answer(msg, show_alert=True)

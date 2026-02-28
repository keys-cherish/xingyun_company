"""Shareholder interaction handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types

from db.engine import async_session
from handlers.common import group_only
from keyboards.menus import invest_kb
from services.shareholder_service import get_shareholders, invest
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_shares, fmt_traffic

router = Router()


@router.callback_query(F.data.startswith("shareholder:list:"), group_only)
async def cb_shareholders(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        shareholders = await get_shareholders(session, company_id)
        # fetch user names
        lines = ["👥 股东列表", "─" * 24]
        for sh in shareholders:
            from db.models import User
            user = await session.get(User, sh.user_id)
            name = user.tg_name if user else "未知"
            lines.append(f"• {name}: {fmt_shares(sh.shares)} (投资: {fmt_traffic(sh.invested_amount)})")

    from keyboards.menus import company_detail_kb
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=company_detail_kb(company_id, False),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shareholder:invest:"), group_only)
async def cb_invest_menu(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    await callback.message.edit_text("选择投资金额:", reply_markup=invest_kb(company_id))
    await callback.answer()


@router.callback_query(F.data.startswith("shareholder:doinvest:"), group_only)
async def cb_do_invest(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    amount = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            ok, msg = await invest(session, user.id, company_id, amount)

    await callback.answer(msg, show_alert=True)

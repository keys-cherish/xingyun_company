"""Real estate handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types

from db.engine import async_session
from handlers.common import group_only
from keyboards.menus import building_list_kb
from services.company_service import get_company_by_id
from services.realestate_service import get_building_list, get_company_estates, purchase_building
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()


@router.callback_query(F.data == "menu:realestate", group_only)
async def cb_realestate_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("🏗 地产投资\n请从公司面板查看地产。")
    await callback.answer()


@router.callback_query(F.data.startswith("realestate:list:"), group_only)
async def cb_estate_list(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return
        estates = await get_company_estates(session, company_id)

    lines = [f"🏗 {company.name} — 地产", "─" * 24]
    if estates:
        total_income = 0
        for e in estates:
            lines.append(f"• {e.building_type} Lv.{e.level} — {fmt_traffic(e.daily_dividend)}/日")
            total_income += e.daily_dividend
        lines.append(f"\n总地产收入: {fmt_traffic(total_income)}/日")
    else:
        lines.append("暂无地产")

    lines.append("\n🏪 可购买地产:")
    buildings = get_building_list()
    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=building_list_kb(buildings, company_id))
    await callback.answer()


@router.callback_query(F.data.startswith("realestate:buy:"), group_only)
async def cb_buy_building(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    building_key = parts[3]
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能购买地产", show_alert=True)
                return
            ok, msg = await purchase_building(session, company_id, tg_id, building_key)

    await callback.answer(msg, show_alert=True)

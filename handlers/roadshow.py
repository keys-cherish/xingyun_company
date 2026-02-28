"""Roadshow handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.roadshow_service import do_roadshow
from services.user_service import get_user_by_tg_id

router = Router()


@router.callback_query(F.data == "menu:roadshow")
async def cb_roadshow_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("🎤 路演\n请从公司面板发起路演。")
    await callback.answer()


@router.callback_query(F.data.startswith("roadshow:do:"))
async def cb_do_roadshow(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能路演", show_alert=True)
                return
            ok, msg = await do_roadshow(session, company_id, user.id)

    if ok:
        await callback.message.edit_text(f"🎤 路演结果\n\n{msg}", reply_markup=main_menu_kb())
        await callback.answer()
    else:
        await callback.answer(msg, show_alert=True)

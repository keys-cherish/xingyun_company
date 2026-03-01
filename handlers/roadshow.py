"""Roadshow handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types

from db.engine import async_session
from keyboards.menus import main_menu_kb, tag_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.roadshow_service import do_roadshow
from services.user_service import get_user_by_tg_id

router = Router()


@router.callback_query(F.data == "menu:roadshow")
async def cb_roadshow_menu(callback: types.CallbackQuery):
    """Auto-select company for roadshow if only one, otherwise show selector."""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /create_company 创建公司", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你还没有公司", show_alert=True)
        return

    if len(companies) == 1:
        await cb_do_roadshow(callback, companies[0].id)
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"roadshow:do:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:company")])
    await callback.message.edit_text(
        "🎤 选择公司发起路演:",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("roadshow:do:"))
async def cb_do_roadshow(callback: types.CallbackQuery, company_id: int | None = None):
    if company_id is None:
        company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /create_company 创建公司", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能路演", show_alert=True)
                return
            ok, msg = await do_roadshow(session, company_id, user.id)

    if ok:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        result_kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回公司", callback_data=f"company:view:{company_id}")],
        ]), callback.from_user.id)
        await callback.message.edit_text(f"🎤 路演结果\n\n{msg}", reply_markup=result_kb)
        await callback.answer()
    else:
        await callback.answer(msg, show_alert=True)

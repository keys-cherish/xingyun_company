"""Cooperation handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.cooperation_service import create_cooperation, get_active_cooperations
from services.user_service import get_user_by_tg_id

router = Router()


class CoopState(StatesGroup):
    waiting_partner_company_id = State()


@router.callback_query(F.data == "menu:cooperation")
async def cb_coop_menu(callback: types.CallbackQuery):
    """Auto-select company for cooperation if only one, otherwise show selector."""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /start 注册", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你还没有公司", show_alert=True)
        return

    if len(companies) == 1:
        from aiogram.fsm.context import FSMContext
        callback.data = f"cooperation:init:{companies[0].id}"
        # Can't easily forward to cb_init_coop without state param, show selector
        pass

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"cooperation:init:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    await callback.message.edit_text(
        "🤝 选择公司发起合作:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cooperation:init:"))
async def cb_init_coop(callback: types.CallbackQuery, state: FSMContext):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /start 注册", show_alert=True)
            return
        company = await get_company_by_id(session, company_id)
        if not company or company.owner_id != user.id:
            await callback.answer("只有公司老板才能发起合作", show_alert=True)
            return

        # Show current cooperations
        coops = await get_active_cooperations(session, company_id)
        lines = [f"🤝 {company.name} 当前合作:", "─" * 24]
        if coops:
            for c in coops:
                partner_id = c.company_b_id if c.company_a_id == company_id else c.company_a_id
                partner = await get_company_by_id(session, partner_id)
                pname = partner.name if partner else "未知"
                lines.append(f"• {pname} (+{c.bonus_multiplier*100:.0f}% 到期:{c.expires_at.strftime('%m-%d')})")
        else:
            lines.append("暂无合作")

    lines.append("\n请输入对方公司ID来发起合作:")
    await callback.message.edit_text("\n".join(lines))
    await state.set_state(CoopState.waiting_partner_company_id)
    await state.update_data(company_id=company_id)
    await callback.answer()


@router.message(CoopState.waiting_partner_company_id)
async def on_partner_id(message: types.Message, state: FSMContext):
    data = await state.get_data()
    company_id = data["company_id"]

    try:
        partner_id = int(message.text.strip())
    except ValueError:
        await message.answer("请输入有效的公司ID (数字):")
        return

    async with async_session() as session:
        async with session.begin():
            ok, msg = await create_cooperation(session, company_id, partner_id)

    await message.answer(msg, reply_markup=main_menu_kb())
    await state.clear()

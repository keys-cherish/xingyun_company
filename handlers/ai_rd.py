"""AI研发交互处理器（仅群组）。

玩家提交产品方案 → AI评分 → 可选招聘研发人员加速 → 永久提升产品收入。
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db.engine import async_session
from keyboards.menus import main_menu_kb, tag_kb
from services.ai_rd_service import (
    MAX_EXTRA_RD_STAFF,
    R_AND_D_COST_PER_STAFF,
    apply_rd_result,
    evaluate_proposal_ai,
)
from services.company_service import add_funds, get_company_by_id
from services.product_service import get_company_products
from services.user_service import get_user_by_tg_id
from utils.panel_owner import mark_panel

router = Router()


class AIRDState(StatesGroup):
    select_product = State()
    waiting_proposal = State()
    waiting_staff = State()


@router.callback_query(F.data.startswith("aird:start:"))
async def cb_aird_start(callback: types.CallbackQuery, state: FSMContext):
    """开始AI研发流程：先选择要研发的产品。"""
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        company = await get_company_by_id(session, company_id)
        if not company or not user or company.owner_id != user.id:
            await callback.answer("只有公司老板才能发起研发", show_alert=True)
            return
        products = await get_company_products(session, company_id)

    if not products:
        await callback.answer("公司还没有产品，请先创建产品", show_alert=True)
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(
            text=f"{p.name} v{p.version} (日收入:{p.daily_income})",
            callback_data=f"aird:select:{p.id}",
        )]
        for p in products
    ]
    buttons.append([InlineKeyboardButton(text="🔙 取消", callback_data=f"company:view:{company_id}")])

    await callback.message.edit_text(
        "🧪 AI产品研发\n选择要进行研发的产品:",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await state.set_state(AIRDState.select_product)
    await state.update_data(company_id=company_id)
    await callback.answer()


@router.callback_query(AIRDState.select_product, F.data.startswith("aird:select:"))
async def cb_aird_select(callback: types.CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(AIRDState.waiting_proposal)
    await callback.message.edit_text(
        "🧪 AI产品研发\n\n"
        "请输入你的产品方案（可无限次研发，无冷却）:\n"
        "• 描述产品功能和创新点\n"
        "• 阐述市场定位和目标用户\n"
        "• 说明商业模式和盈利方式\n"
        "• 分析技术可行性与合规风险\n"
        "• 给出可量化指标（转化、留存、ROI等）\n\n"
        "AI将采用【严格文案批判标准】：\n"
        "先指出硬伤，再给分项评分和改进建议。\n"
        "评分越高，产品收入永久提升越多。"
    )
    await callback.answer()


@router.message(AIRDState.waiting_proposal)
async def on_proposal(message: types.Message, state: FSMContext):
    proposal = (message.text or "").strip()
    if len(proposal) < 10:
        await message.answer("方案描述太短，请至少写10个字:")
        return

    # Evaluate
    score, feedback, special_effect = await evaluate_proposal_ai(proposal)
    await state.update_data(score=score, feedback=feedback, special_effect=special_effect)
    await state.set_state(AIRDState.waiting_staff)
    special_preview = f"特殊效果: {special_effect}" if special_effect else "特殊效果: 无"

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text="不招聘，直接研发", callback_data="aird:staff:0")],
        [InlineKeyboardButton(text=f"招3人 (花费{3*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:3")],
        [InlineKeyboardButton(text=f"招5人 (花费{5*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:5")],
        [InlineKeyboardButton(text=f"招10人 (花费{10*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:10")],
    ]

    data = await state.get_data()
    company_id = data["company_id"]
    buttons.append([InlineKeyboardButton(text="🔙 取消", callback_data=f"company:view:{company_id}")])

    sent = await message.answer(
        f"🧪 AI评估结果\n"
        f"{'─' * 24}\n"
        f"评分: {score}/100\n"
        f"{feedback}\n"
        f"{special_preview}\n\n"
        f"预计收入提升: 约{score}%\n\n"
        "是否招聘额外研发人员加速研发？\n"
        "(每名研发人员+5%研发效率)",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), message.from_user.id),
    )
    await mark_panel(message.chat.id, sent.message_id, message.from_user.id)


@router.callback_query(AIRDState.waiting_staff, F.data.startswith("aird:staff:"))
async def cb_aird_staff(callback: types.CallbackQuery, state: FSMContext):
    extra_staff = max(0, min(int(callback.data.split(":")[2]), MAX_EXTRA_RD_STAFF))
    data = await state.get_data()
    company_id = data["company_id"]
    product_id = data["product_id"]
    score = data["score"]
    special_effect = data.get("special_effect")
    tg_id = callback.from_user.id

    staff_cost = extra_staff * R_AND_D_COST_PER_STAFF

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("用户不存在", show_alert=True)
                await state.clear()
                return

            # 二次校验公司归属
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("无权操作此公司", show_alert=True)
                await state.clear()
                return

            # Deduct staff cost from company
            if staff_cost > 0:
                ok = await add_funds(session, company_id, -staff_cost)
                if not ok:
                    await callback.answer(f"公司资金不足，需要 {staff_cost:,} 积分", show_alert=True)
                    return

            ok, msg, income_increase = await apply_rd_result(
                session, product_id, user.id, score, extra_staff, special_effect=special_effect
            )

    await state.clear()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup as IKM
    result_kb = tag_kb(IKM(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回公司", callback_data=f"company:view:{company_id}")],
    ]), tg_id)
    if ok:
        await callback.message.edit_text(
            f"🧪 研发完成!\n─" + "─" * 23 + f"\n{msg}",
            reply_markup=result_kb,
        )
        await callback.answer()
    else:
        await callback.answer(msg, show_alert=True)

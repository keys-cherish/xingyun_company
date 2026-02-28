"""AI研发交互处理器（仅群组）。

玩家提交产品方案 → AI评分 → 可选招聘研发人员加速 → 永久提升产品收入。
"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db.engine import async_session
from handlers.common import group_only
from keyboards.menus import main_menu_kb
from services.ai_rd_service import (
    R_AND_D_COST_PER_STAFF,
    apply_rd_result,
    evaluate_proposal_ai,
)
from services.company_service import add_funds, get_company_by_id
from services.product_service import get_company_products
from services.user_service import add_traffic, get_user_by_tg_id

router = Router()


class AIRDState(StatesGroup):
    select_product = State()
    waiting_proposal = State()
    waiting_staff = State()


@router.callback_query(F.data.startswith("aird:start:"), group_only)
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
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(AIRDState.select_product)
    await state.update_data(company_id=company_id)
    await callback.answer()


@router.callback_query(AIRDState.select_product, F.data.startswith("aird:select:"), group_only)
async def cb_aird_select(callback: types.CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(AIRDState.waiting_proposal)
    await callback.message.edit_text(
        "🧪 AI产品研发\n\n"
        "请输入你的产品方案（越详细评分越高）:\n"
        "• 描述产品功能和创新点\n"
        "• 阐述市场定位和目标用户\n"
        "• 说明商业模式和盈利方式\n"
        "• 分析技术可行性\n\n"
        "AI将从创新性、市场可行性、技术可行性、商业价值四个维度评分(1-100分)。\n"
        "评分越高，产品收入永久提升越多！"
    )
    await callback.answer()


@router.message(AIRDState.waiting_proposal, group_only)
async def on_proposal(message: types.Message, state: FSMContext):
    proposal = message.text.strip()
    if len(proposal) < 10:
        await message.answer("方案描述太短，请至少写10个字:")
        return

    # Evaluate
    score, feedback = await evaluate_proposal_ai(proposal)
    await state.update_data(score=score, feedback=feedback)
    await state.set_state(AIRDState.waiting_staff)

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text="不招聘，直接研发", callback_data="aird:staff:0")],
        [InlineKeyboardButton(text=f"招3人 (花费{3*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:3")],
        [InlineKeyboardButton(text=f"招5人 (花费{5*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:5")],
        [InlineKeyboardButton(text=f"招10人 (花费{10*R_AND_D_COST_PER_STAFF}💰)", callback_data="aird:staff:10")],
    ]

    await message.answer(
        f"🧪 AI评估结果\n"
        f"─" * 24 + "\n"
        f"评分: {score}/100\n"
        f"评价: {feedback}\n\n"
        f"预计收入提升: ~{score}%\n\n"
        "是否招聘额外研发人员加速研发？\n"
        "(每名研发人员+5%研发效率)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(AIRDState.waiting_staff, F.data.startswith("aird:staff:"), group_only)
async def cb_aird_staff(callback: types.CallbackQuery, state: FSMContext):
    extra_staff = int(callback.data.split(":")[2])
    data = await state.get_data()
    company_id = data["company_id"]
    product_id = data["product_id"]
    score = data["score"]
    tg_id = callback.from_user.id

    staff_cost = extra_staff * R_AND_D_COST_PER_STAFF

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("用户不存在", show_alert=True)
                await state.clear()
                return

            # Deduct staff cost from company
            if staff_cost > 0:
                ok = await add_funds(session, company_id, -staff_cost)
                if not ok:
                    await callback.answer(f"公司资金不足，需要{staff_cost}流量", show_alert=True)
                    return

            ok, msg, income_increase = await apply_rd_result(
                session, product_id, user.id, score, extra_staff
            )

    await state.clear()
    if ok:
        await callback.message.edit_text(
            f"🧪 研发完成!\n─" + "─" * 23 + f"\n{msg}",
            reply_markup=main_menu_kb(),
        )
    else:
        await callback.answer(msg, show_alert=True)
    await callback.answer()

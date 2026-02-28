"""Cooperation handlers – inline menu + /cooperate command."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.cooperation_service import (
    cooperate_all,
    cooperate_with,
    create_cooperation,
    get_active_cooperations,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()
logger = logging.getLogger(__name__)


class CoopState(StatesGroup):
    waiting_partner_company_id = State()


# ---- /cooperate command ----

@router.message(Command("cooperate"))
async def cmd_cooperate(message: types.Message):
    """Handle /cooperate all | /cooperate <company_id> | reply to cooperate."""
    tg_id = message.from_user.id
    args = (message.text or "").split(maxsplit=1)
    arg = args[1].strip() if len(args) > 1 else ""

    # 回复消息合作：回复某人消息并发 /cooperate
    if not arg and message.reply_to_message:
        target = message.reply_to_message.from_user
        if not target or target.is_bot:
            await message.answer("❌ 不能与机器人合作")
            return
        if target.id == tg_id:
            await message.answer("❌ 不能与自己合作")
            return

        try:
            async with async_session() as session:
                async with session.begin():
                    user = await get_user_by_tg_id(session, tg_id)
                    target_user = await get_user_by_tg_id(session, target.id)
                    if not user:
                        await message.answer("请先 /start 注册")
                        return
                    if not target_user:
                        await message.answer("❌ 对方还未注册")
                        return
                    my_companies = await get_companies_by_owner(session, user.id)
                    target_companies = await get_companies_by_owner(session, target_user.id)
                    if not my_companies:
                        await message.answer("你还没有公司")
                        return
                    if not target_companies:
                        await message.answer("❌ 对方没有公司")
                        return
                    ok, msg = await cooperate_with(session, my_companies[0].id, target_companies[0].id)
            await message.answer(msg)
        except Exception:
            logger.exception("cooperate reply error")
            await message.answer("❌ 合作失败，请稍后重试")
        return

    if not arg:
        await message.answer(
            "🤝 合作命令:\n"
            "  /cooperate — 回复某人消息直接合作\n"
            "  /cooperate all — 一键与所有公司合作\n"
            "  /cooperate <公司ID> — 与指定公司合作\n"
            "合作加成每次+5%，次日结算后清空需重新合作\n"
            "普通公司上限50%，满级公司上限100%"
        )
        return

    try:
        if arg.lower() == "all":
            async with async_session() as session:
                async with session.begin():
                    user = await get_user_by_tg_id(session, tg_id)
                    if not user:
                        await message.answer("请先 /start 注册")
                        return
                    companies = await get_companies_by_owner(session, user.id)
                    if not companies:
                        await message.answer("你还没有公司")
                        return
                    my_company = companies[0]
                    success, skip, msgs = await cooperate_all(session, my_company.id)
                    company_name = my_company.name

            lines = [
                f"🤝 「{company_name}」一键合作完成",
                f"新增合作: {success} 家",
            ]
            if skip > 0:
                lines.append(f"跳过: {skip} 家（已合作或达上限）")
            if msgs:
                lines.extend(msgs)
            await message.answer("\n".join(lines))
        else:
            try:
                target_id = int(arg)
            except ValueError:
                await message.answer("请输入有效的公司ID (数字) 或 all")
                return
            async with async_session() as session:
                async with session.begin():
                    user = await get_user_by_tg_id(session, tg_id)
                    if not user:
                        await message.answer("请先 /start 注册")
                        return
                    companies = await get_companies_by_owner(session, user.id)
                    if not companies:
                        await message.answer("你还没有公司")
                        return
                    ok, msg = await cooperate_with(session, companies[0].id, target_id)
            await message.answer(msg)
    except Exception:
        logger.exception("cooperate command error")
        await message.answer("❌ 合作操作失败，请稍后重试")


# ---- Inline menu handlers (legacy) ----

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

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"cooperation:init:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    await callback.message.edit_text(
        "🤝 选择公司发起合作:\n\n"
        "💡 也可以使用命令:\n"
        "  /cooperate all — 一键全部合作\n"
        "  /cooperate <公司ID> — 指定合作",
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

        coops = await get_active_cooperations(session, company_id)
        current_total = sum(c.bonus_multiplier for c in coops)
        lines = [f"🤝 {company.name} 当前合作 (加成: {current_total*100:.0f}%):", f"{'─' * 24}"]
        if coops:
            for c in coops:
                partner_id = c.company_b_id if c.company_a_id == company_id else c.company_a_id
                partner = await get_company_by_id(session, partner_id)
                pname = partner.name if partner else "未知"
                lines.append(f"• {pname} (+{c.bonus_multiplier*100:.0f}%)")
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

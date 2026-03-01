"""Shareholder interaction handlers (group only)."""

from __future__ import annotations

import time

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from commands import CMD_CANCEL
from db.engine import async_session
from keyboards.menus import invest_kb, shareholder_list_kb
from services.shareholder_service import get_shareholders, invest
from utils.panel_owner import mark_panel
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_shares, fmt_traffic

router = Router()
INVEST_INPUT_TIMEOUT_SECONDS = 5 * 60


class InvestState(StatesGroup):
    waiting_custom_amount = State()


async def _refresh_shareholder_list(callback: types.CallbackQuery, company_id: int):
    """操作后刷新股东列表消息。"""
    tg_id = callback.from_user.id
    try:
        async with async_session() as session:
            shareholders = await get_shareholders(session, company_id)
            lines = ["👥 股东列表", "─" * 24]
            for sh in shareholders:
                from db.models import User
                user = await session.get(User, sh.user_id)
                name = user.tg_name if user else "未知"
                lines.append(f"• {name}: {fmt_shares(sh.shares)} (注资: {fmt_traffic(sh.invested_amount)})")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=shareholder_list_kb(company_id, tg_id=tg_id),
        )
    except Exception:
        pass  # 消息未变化时edit会抛异常，忽略


@router.callback_query(F.data.startswith("shareholder:list:"))
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
            lines.append(f"• {name}: {fmt_shares(sh.shares)} (注资: {fmt_traffic(sh.invested_amount)})")
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=shareholder_list_kb(company_id, tg_id=callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shareholder:invest:"))
async def cb_invest_menu(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    await callback.message.edit_text("选择注资金额:", reply_markup=invest_kb(company_id, tg_id=callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("shareholder:input:"))
async def cb_invest_input(callback: types.CallbackQuery, state: FSMContext):
    company_id = int(callback.data.split(":")[2])

    await state.set_state(InvestState.waiting_custom_amount)
    await state.update_data(company_id=company_id, started_ts=int(time.time()))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ 取消输入", callback_data=f"shareholder:input_cancel:{company_id}")],
        [InlineKeyboardButton(text="🔙 返回注资面板", callback_data=f"shareholder:invest:{company_id}")],
    ])
    await callback.message.edit_text(
        "✍️ 自定义注资金额\n"
        "请输入注资金额（整数，如 5000）\n"
        f"⏳ {INVEST_INPUT_TIMEOUT_SECONDS // 60} 分钟内未输入将自动退出",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shareholder:input_cancel:"))
async def cb_invest_input_cancel(callback: types.CallbackQuery, state: FSMContext):
    company_id = int(callback.data.split(":")[2])
    await state.clear()
    await callback.message.edit_text("选择注资金额:", reply_markup=invest_kb(company_id, tg_id=callback.from_user.id))
    await callback.answer("已取消输入")


@router.message(InvestState.waiting_custom_amount, Command(CMD_CANCEL))
async def on_invest_input_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("已取消注资输入。")


@router.message(InvestState.waiting_custom_amount)
async def on_custom_invest_amount(message: types.Message, state: FSMContext):
    data = await state.get_data()
    company_id = int(data.get("company_id", 0))
    started_ts = int(data.get("started_ts", 0))
    now = int(time.time())

    if company_id <= 0:
        await state.clear()
        await message.answer("注资状态异常，已退出。")
        return

    if started_ts <= 0 or now - started_ts > INVEST_INPUT_TIMEOUT_SECONDS:
        await state.clear()
        await message.answer(
            f"⏳ 注资输入超时（>{INVEST_INPUT_TIMEOUT_SECONDS // 60}分钟），已自动退出。"
        )
        return

    text = (message.text or "").strip()
    if text.startswith("/"):
        await state.clear()
        await message.answer("已退出注资输入模式。请重新发送命令继续。")
        return

    amount_str = text.replace(",", "").replace("_", "")
    try:
        amount = int(amount_str)
    except ValueError:
        left = max(1, INVEST_INPUT_TIMEOUT_SECONDS - (now - started_ts))
        await message.answer(
            f"请输入有效金额（整数，例如 5000）。剩余时间约 {left // 60}分{left % 60}秒"
        )
        return

    tg_id = message.from_user.id
    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await state.clear()
                await message.answer("请先 /company_create 创建公司")
                return
            ok, msg = await invest(session, user.id, company_id, amount)

    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="继续注资", callback_data=f"shareholder:invest:{company_id}")],
        [InlineKeyboardButton(text="返回公司", callback_data=f"company:view:{company_id}")],
    ])
    sent = await message.answer(msg, reply_markup=kb)
    await mark_panel(message.chat.id, sent.message_id, message.from_user.id)


@router.callback_query(F.data.startswith("shareholder:doinvest:"))
async def cb_do_invest(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    amount = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            ok, msg = await invest(session, user.id, company_id, amount)

    await callback.answer(msg, show_alert=True)
    if ok:
        await _refresh_shareholder_list(callback, company_id)

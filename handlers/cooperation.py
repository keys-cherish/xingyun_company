"""Cooperation handlers – command flow + non-blocking inline panels."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from services.company_service import get_companies_by_owner, get_company_by_id
from services.cooperation_service import (
    cooperate_all,
    cooperate_with,
    get_active_cooperations,
)
from services.user_service import get_user_by_tg_id

router = Router()
logger = logging.getLogger(__name__)


def _parse_init_data(data: str) -> tuple[str, int]:
    """
    Parse callback data for cooperation init.
    Supports both new format and backward compatible legacy format:
    - cooperation:init:menu:<company_id>
    - cooperation:init:<company_id>
    """
    parts = data.split(":")
    if len(parts) == 4:
        return parts[2], int(parts[3])
    if len(parts) == 3:
        return "company", int(parts[2])
    raise ValueError("invalid cooperation init callback")


def _overview_kb(company_id: int, source: str) -> InlineKeyboardMarkup:
    back_cb = "menu:cooperation" if source == "menu" else f"company:view:{company_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 发起合作", callback_data=f"cooperation:action:{source}:{company_id}")],
            [InlineKeyboardButton(text="🔙 返回", callback_data=back_cb)],
        ]
    )


def _action_kb(company_id: int, source: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ 一键全部合作", callback_data=f"cooperation:doall:{source}:{company_id}")],
            [InlineKeyboardButton(text="🔙 返回合作面板", callback_data=f"cooperation:init:{source}:{company_id}")],
        ]
    )


def _result_kb(company_id: int, source: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 返回合作面板", callback_data=f"cooperation:init:{source}:{company_id}")],
            [InlineKeyboardButton(text="🏠 返回公司", callback_data="menu:company")],
        ]
    )


async def _get_owned_company(
    session,
    tg_id: int,
    company_id: int,
):
    user = await get_user_by_tg_id(session, tg_id)
    if not user:
        return None, "请先 /create_company 创建公司"
    company = await get_company_by_id(session, company_id)
    if not company or company.owner_id != user.id:
        return None, "只有公司老板才能操作合作"
    return company, ""


async def _build_coop_overview_text(session, company_id: int) -> str:
    company = await get_company_by_id(session, company_id)
    if not company:
        return "❌ 公司不存在"

    coops = await get_active_cooperations(session, company_id)
    current_total = sum(c.bonus_multiplier for c in coops)
    lines = [
        f"🤝 {company.name} 合作面板",
        f"{'─' * 24}",
        f"当前合作加成: +{current_total * 100:.0f}%",
        f"合作公司数量: {len(coops)}",
        f"{'─' * 24}",
    ]
    if coops:
        for c in coops:
            partner_id = c.company_b_id if c.company_a_id == company_id else c.company_a_id
            partner = await get_company_by_id(session, partner_id)
            partner_name = partner.name if partner else f"未知公司#{partner_id}"
            lines.append(f"• {partner_name} (+{c.bonus_multiplier * 100:.0f}%)")
    else:
        lines.append("暂无合作公司")

    lines += [
        "",
        "💡 默认仅查看，不会自动发起合作",
        "点击下方「发起合作」再选择具体操作",
    ]
    return "\n".join(lines)


# ---- /cooperate command ----

@router.message(Command("cooperate"))
async def cmd_cooperate(message: types.Message):
    """Handle /cooperate all | /cooperate <company_id> | reply to cooperate."""
    tg_id = message.from_user.id
    args = (message.text or "").split(maxsplit=1)
    arg = args[1].strip() if len(args) > 1 else ""

    # Reply-to cooperation: reply to someone and send /cooperate
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
                        await message.answer("请先 /create_company 创建公司")
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
                        await message.answer("请先 /create_company 创建公司")
                        return
                    companies = await get_companies_by_owner(session, user.id)
                    if not companies:
                        await message.answer("你还没有公司，请先使用 /create_company 创建")
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
            await message.answer("请使用 /cooperate all 一键合作，或回复某人消息 /cooperate 直接合作")
    except Exception:
        logger.exception("cooperate command error")
        await message.answer("❌ 合作操作失败，请稍后重试")


# ---- Inline cooperation panels ----

@router.callback_query(F.data == "menu:cooperation")
async def cb_coop_menu(callback: types.CallbackQuery):
    """Show company selector first."""
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

    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"cooperation:init:menu:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:company")])

    await callback.message.edit_text(
        "🤝 选择公司查看合作面板:\n\n"
        "💡 也可以使用命令:\n"
        "  /cooperate all — 一键全部合作\n"
        "  /cooperate — 回复某人消息直接合作",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cooperation:init:"))
async def cb_init_coop(callback: types.CallbackQuery, state: FSMContext):
    """Show cooperation overview only; no forced input."""
    try:
        source, company_id = _parse_init_data(callback.data)
    except Exception:
        await callback.answer("面板已过期，请重试", show_alert=True)
        return

    tg_id = callback.from_user.id
    await state.clear()  # Ensure no lingering input state blocks future actions.

    async with async_session() as session:
        company, err = await _get_owned_company(session, tg_id, company_id)
        if not company:
            await callback.answer(err, show_alert=True)
            return
        text = await _build_coop_overview_text(session, company_id)

    await callback.message.edit_text(text, reply_markup=_overview_kb(company_id, source))
    await callback.answer()


@router.callback_query(F.data.startswith("cooperation:action:"))
async def cb_coop_action(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("面板已过期，请重试", show_alert=True)
        return
    source = parts[2]
    company_id = int(parts[3])

    tg_id = callback.from_user.id
    async with async_session() as session:
        company, err = await _get_owned_company(session, tg_id, company_id)
        if not company:
            await callback.answer(err, show_alert=True)
            return

    await callback.message.edit_text(
        f"🚀 {company.name} 发起合作\n"
        f"{'─' * 24}\n"
        "请选择合作方式:",
        reply_markup=_action_kb(company_id, source),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cooperation:doall:"))
async def cb_coop_doall(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("面板已过期，请重试", show_alert=True)
        return
    source = parts[2]
    company_id = int(parts[3])
    tg_id = callback.from_user.id

    try:
        async with async_session() as session:
            async with session.begin():
                company, err = await _get_owned_company(session, tg_id, company_id)
                if not company:
                    await callback.answer(err, show_alert=True)
                    return
                success, skip, msgs = await cooperate_all(session, company.id)

        lines = [
            f"🤝 「{company.name}」一键合作完成",
            f"新增合作: {success} 家",
        ]
        if skip > 0:
            lines.append(f"跳过: {skip} 家（已合作或达上限）")
        if msgs:
            lines.extend(msgs)
        await callback.message.edit_text("\n".join(lines), reply_markup=_result_kb(company_id, source))
        await callback.answer()
    except Exception:
        logger.exception("cooperate all from panel error")
        await callback.answer("合作失败，请稍后重试", show_alert=True)


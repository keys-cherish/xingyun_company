"""Cooperation handlers – /company_cooperate command + reply-based '合作' trigger."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from commands import CMD_COOPERATE
from db.engine import async_session
from keyboards.menus import tag_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.cooperation_service import (
    cooperate_all,
    cooperate_with,
    get_active_cooperations,
)
from services.user_service import get_user_by_tg_id

router = Router()
logger = logging.getLogger(__name__)


async def _do_reply_cooperate(message: types.Message):
    """Common logic for reply-based cooperation (both /company_cooperate and '合作')."""
    tg_id = message.from_user.id
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
                    await message.answer("请先 /company_start 注册")
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


# ---- /company_cooperate command ----

@router.message(Command(CMD_COOPERATE))
async def cmd_cooperate(message: types.Message):
    """Handle /company_cooperate all | /company_cooperate <company_id> | reply to cooperate."""
    tg_id = message.from_user.id
    args = (message.text or "").split(maxsplit=1)
    arg = args[1].strip() if len(args) > 1 else ""

    # Reply-to cooperation: reply to someone and send /company_cooperate
    if not arg and message.reply_to_message:
        await _do_reply_cooperate(message)
        return

    if not arg:
        await message.answer(
            "🤝 合作命令:\n"
            "  回复某人消息 + 发送「合作」— 直接合作\n"
            "  /company_cooperate all — 一键与所有公司合作\n"
            "每次合作+2%营收（上限50%），次日结算后清空需重新合作\n"
            "双方各 +30 声望\n"
            "合作数量不限，但buff上限50%"
        )
        return

    try:
        if arg.lower() == "all":
            async with async_session() as session:
                async with session.begin():
                    user = await get_user_by_tg_id(session, tg_id)
                    if not user:
                        await message.answer("请先 /company_create 创建公司")
                        return
                    companies = await get_companies_by_owner(session, user.id)
                    if not companies:
                        await message.answer("你还没有公司，请先使用 /company_create 创建")
                        return
                    my_company = companies[0]
                    success, skip, msgs = await cooperate_all(session, my_company.id)
                    company_name = my_company.name

            lines = [
                f"🤝 「{company_name}」一键合作完成",
                f"新增合作: {success} 家",
            ]
            if skip > 0:
                lines.append(f"已合作跳过: {skip} 家")
            if msgs:
                lines.extend(msgs)
            await message.answer("\n".join(lines))
        else:
            await message.answer("请使用 /company_cooperate all 一键合作，或回复某人消息 /company_cooperate 直接合作")
    except Exception:
        logger.exception("cooperate command error")
        await message.answer("❌ 合作操作失败，请稍后重试")


# ---- Chinese trigger: reply + "合作" ----

@router.message(F.text == "合作")
async def cmd_cooperate_chinese(message: types.Message):
    """Reply to someone's message and type '合作' to cooperate."""
    if not message.reply_to_message:
        await message.answer(
            "💡 回复某人的消息并发送「合作」即可合作\n"
            "或使用 /company_cooperate all 一键合作"
        )
        return
    await _do_reply_cooperate(message)


# ---- Inline menu handlers ----

@router.callback_query(F.data == "menu:cooperation")
async def cb_coop_menu(callback: types.CallbackQuery):
    """Show company selector first."""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你还没有公司", show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"cooperation:init:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)
    await callback.message.edit_text(
        "🤝 选择公司查看合作状态:\n\n"
        "💡 也可以使用命令:\n"
        "  /company_cooperate all — 一键全部合作\n"
        "  回复某人消息 + 发送「合作」",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cooperation:init:"))
async def cb_init_coop(callback: types.CallbackQuery):
    """Show cooperation status for a company (no longer enters FSM)."""
    parts = callback.data.split(":")
    company_id = int(parts[-1])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_start 注册", show_alert=True)
            return
        company = await get_company_by_id(session, company_id)
        if not company or company.owner_id != user.id:
            await callback.answer("只有公司老板才能查看合作", show_alert=True)
            return

        coops = await get_active_cooperations(session, company_id)
        current_total = sum(c.bonus_multiplier for c in coops)
        lines = [f"🤝 {company.name} 合作状态 (加成: {current_total*100:.0f}%)", f"{'─' * 24}"]
        if coops:
            for c in coops:
                partner_id = c.company_b_id if c.company_a_id == company_id else c.company_a_id
                partner = await get_company_by_id(session, partner_id)
                pname = partner.name if partner else "未知"
                lines.append(f"• {pname} (+{c.bonus_multiplier*100:.0f}%)")
        else:
            lines.append("暂无合作")

    lines.append(f"\n💡 合作方式:")
    lines.append(f"  • 回复某人消息 + 发送「合作」")
    lines.append(f"  • /company_cooperate all — 一键全部合作")
    lines.append(f"\n🎁 合作收益:")
    lines.append(f"  • 当日合作Buff：每次 +2% 营收（上限50%）")
    lines.append(f"  • 成功合作双方各 +30 声望")

    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")],
    ]), tg_id)
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()

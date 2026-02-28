"""/start handler: registration, main menu, and new-user company creation guide."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.user_service import get_or_create_user, get_points
from services.company_service import get_companies_by_owner, load_company_types
from utils.formatters import fmt_reputation_buff, fmt_traffic

router = Router()


def _company_type_kb() -> types.InlineKeyboardMarkup:
    """公司类型选择键盘（用于新用户引导）。"""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    types_data = load_company_types()
    buttons = [
        [InlineKeyboardButton(
            text=f"{info['emoji']} {info['name']}",
            callback_data=f"company:type:{key}",
        )]
        for key, info in types_data.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Register or greet existing user. Works in both private and group."""
    tg_id = message.from_user.id
    tg_name = message.from_user.full_name

    async with async_session() as session:
        async with session.begin():
            user, created = await get_or_create_user(session, tg_id, tg_name)

        # 检查是否已有公司
        companies = await get_companies_by_owner(session, user.id)

    if created:
        text = (
            f"欢迎加入星云公司, {tg_name}!\n\n"
            f"你获得了初始流量: {fmt_traffic(user.traffic)}\n"
            f"声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n"
        )
    else:
        points = await get_points(tg_id)
        text = (
            f"欢迎回来, {tg_name}!\n\n"
            f"流量: {fmt_traffic(user.traffic)}\n"
            f"声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n"
            f"积分: {points}\n"
        )

    # 新用户或无公司：引导创建公司
    if not companies:
        types_data = load_company_types()
        type_desc = "\n".join(
            f"{info['emoji']} {info['name']} — {info['description']}"
            for info in types_data.values()
        )
        text += (
            "\n你还没有公司，请选择公司类型创建你的第一家公司:\n\n"
            f"{type_desc}"
        )

        if message.chat.type == "private":
            from handlers.common import is_admin_authenticated
            if await is_admin_authenticated(tg_id):
                # 管理员私聊引导创建
                from aiogram.fsm.context import FSMContext
                await message.answer(text, reply_markup=_company_type_kb())
            else:
                text += "\n\n请在群组中进行创建公司操作。\n管理员请使用 /admin <密钥> 认证。"
                await message.answer(text)
        else:
            await message.answer(text, reply_markup=_company_type_kb())
        return

    # 已有公司：显示主菜单
    if message.chat.type == "private":
        from handlers.common import is_admin_authenticated
        if await is_admin_authenticated(tg_id):
            text += "\n管理员模式已激活。"
            await message.answer(text, reply_markup=main_menu_kb())
        else:
            text += "\n私聊仅支持 /company 查看信息，其他操作请在群组中进行。"
            await message.answer(text)
    else:
        await message.answer(text, reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("主菜单", reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def cb_profile(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    async with async_session() as session:
        from services.user_service import get_user_by_tg_id
        user = await get_user_by_tg_id(session, tg_id)

    if not user:
        await callback.answer("请先使用 /start 注册", show_alert=True)
        return

    points = await get_points(tg_id)

    # Get share holdings
    async with async_session() as session:
        from sqlalchemy import select
        from db.models import Shareholder, Company
        result = await session.execute(
            select(Shareholder, Company).join(Company, Shareholder.company_id == Company.id)
            .where(Shareholder.user_id == user.id)
        )
        holdings = result.all()

    holdings_text = ""
    if holdings:
        holdings_text = "\n持有股份:\n"
        for sh, comp in holdings:
            holdings_text += f"  {comp.name}: {sh.shares:.2f}%\n"

    text = (
        f"个人面板 — {user.tg_name}\n"
        "─" * 24 + "\n"
        f"流量: {fmt_traffic(user.traffic)}\n"
        f"声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n"
        f"积分: {points}\n"
        f"{holdings_text}"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:leaderboard")
async def cb_leaderboard(callback: types.CallbackQuery):
    from cache.redis_client import get_leaderboard

    lb = await get_leaderboard("revenue", 10)
    if not lb:
        text = "排行榜暂无数据"
    else:
        lines = ["营收排行榜 TOP 10", "─" * 24]
        for i, (member, score) in enumerate(lb, 1):
            lines.append(f"{i}. {member}: {int(score):,} MB/日")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:exchange")
async def cb_exchange_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    points = await get_points(tg_id)
    from keyboards.menus import exchange_kb
    text = f"积分兑换\n当前积分: {points}\n兑换比率: 10积分 = 1MB"
    await callback.message.edit_text(text, reply_markup=exchange_kb())
    await callback.answer()


@router.callback_query(F.data.startswith("exchange:"))
async def cb_do_exchange(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    amount = int(callback.data.split(":")[1])

    async with async_session() as session:
        async with session.begin():
            from services.user_service import exchange_points_for_traffic
            ok, msg = await exchange_points_for_traffic(session, tg_id, amount)

    await callback.answer(msg, show_alert=True)
    if ok:
        # refresh exchange menu
        points = await get_points(tg_id)
        from keyboards.menus import exchange_kb
        await callback.message.edit_text(
            f"积分兑换\n当前积分: {points}\n兑换比率: 10积分 = 1流量",
            reply_markup=exchange_kb(),
        )

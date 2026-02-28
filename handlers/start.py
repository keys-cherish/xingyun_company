"""/start handler: registration and main menu."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.user_service import get_or_create_user, get_points
from utils.formatters import fmt_reputation_buff, fmt_traffic

router = Router()


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    """Register or greet existing user. Works in both private and group."""
    tg_id = message.from_user.id
    tg_name = message.from_user.full_name

    async with async_session() as session:
        async with session.begin():
            user, created = await get_or_create_user(session, tg_id, tg_name)

    if created:
        text = (
            f"🎉 欢迎加入星云公司, {tg_name}!\n\n"
            f"你获得了初始流量: {fmt_traffic(user.traffic)}\n"
            f"声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n\n"
            "使用下方菜单开始你的商业帝国之旅!"
        )
    else:
        points = await get_points(tg_id)
        text = (
            f"👋 欢迎回来, {tg_name}!\n\n"
            f"💰 流量: {fmt_traffic(user.traffic)}\n"
            f"⭐ 声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n"
            f"🎯 积分: {points}\n"
        )

    # In group context, show full menu; in private, hint about limited commands
    if message.chat.type == "private":
        text += "\n⚠️ 私聊仅支持 /company 查看公司信息，其他操作请在群组频道中进行。"
        await message.answer(text)
    else:
        await message.answer(text, reply_markup=main_menu_kb())


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("🏠 主菜单", reply_markup=main_menu_kb())
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
        holdings_text = "\n📋 持有股份:\n"
        for sh, comp in holdings:
            holdings_text += f"  • {comp.name}: {sh.shares:.2f}%\n"

    text = (
        f"👤 个人面板 — {user.tg_name}\n"
        "─" * 24 + "\n"
        f"💰 流量: {fmt_traffic(user.traffic)}\n"
        f"⭐ 声望: {user.reputation} ({fmt_reputation_buff(user.reputation)})\n"
        f"🎯 积分: {points}\n"
        f"{holdings_text}"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:leaderboard")
async def cb_leaderboard(callback: types.CallbackQuery):
    from cache.redis_client import get_leaderboard

    lb = await get_leaderboard("revenue", 10)
    if not lb:
        text = "📈 排行榜暂无数据"
    else:
        lines = ["📈 营收排行榜 TOP 10", "─" * 24]
        for i, (member, score) in enumerate(lb, 1):
            lines.append(f"{i}. {member}: {int(score):,} 流量/日")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:exchange")
async def cb_exchange_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    points = await get_points(tg_id)
    from keyboards.menus import exchange_kb
    text = f"🔄 积分兑换\n当前积分: {points}\n兑换比率: 10积分 = 1流量"
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
            f"🔄 积分兑换\n当前积分: {points}\n兑换比率: 10积分 = 1流量",
            reply_markup=exchange_kb(),
        )

"""Start, help, profile, leaderboard handlers."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import BotCommand

from cache.redis_client import get_leaderboard
from config import settings
from db.engine import async_session
from keyboards.menus import main_menu_kb, start_existing_user_kb
from services.company_service import get_companies_by_owner
from services.user_service import get_or_create_user, get_points, get_quota_mb
from utils.formatters import fmt_traffic, fmt_quota, compact_number

router = Router()

BOT_COMMANDS = [
    BotCommand(command="start", description="开始游戏 / 个人面板"),
    BotCommand(command="company", description="我的公司"),
    BotCommand(command="list_company", description="查看全服公司"),
    BotCommand(command="battle", description="商战（回复某人消息）"),
    BotCommand(command="cooperate", description="合作（all/公司ID）"),
    BotCommand(command="new_product", description="研发产品（名字 资金 人员）"),
    BotCommand(command="member", description="员工管理（add/minus 数量）"),
    BotCommand(command="help", description="帮助信息"),
]

HELP_TEXT = (
    "🏢 商业帝国 — 公司经营模拟游戏\n"
    f"{'─' * 24}\n"
    "通过 科研→产品→利润 的路径经营虚拟公司\n\n"
    "📋 命令列表:\n\n"
    "/start\n"
    "  注册账号 / 查看个人面板\n\n"
    "/company\n"
    "  查看和管理你的公司\n\n"
    "/list_company\n"
    "  查看全服所有公司（按资金排序）\n\n"
    "⚔️ /battle\n"
    "  回复某人的消息发起商战\n"
    "  根据公司实力自动PK，胜者掠夺败者资金\n"
    "  冷却时间: 30分钟\n\n"
    "🤝 /cooperate <参数>\n"
    "  /cooperate all — 一键与所有公司合作\n"
    "  /cooperate 3001 — 与公司ID 3001 合作\n"
    "  每次合作+10%营收，次日结算后清空\n"
    "  普通公司上限50%，满级公司上限100%\n\n"
    "📦 /new_product <名字> <资金> <人员>\n"
    "  例: /new_product 智能助手 10000 3\n"
    "  投入资金决定基础日收入，人员提供加成\n"
    "  资金范围: 1,000 ~ 500,000\n\n"
    "👷 /member <操作> <数量>\n"
    "  /member add 5 — 招聘5人\n"
    "  /member add max — 招满\n"
    "  /member minus 3 — 裁员3人\n\n"
    "/admin <密钥>\n"
    "  管理员认证（需配置ID+密钥）\n\n"
    "/help\n"
    "  显示此帮助信息\n"
)


@router.message(Command("start"))
async def cmd_start(message: types.Message):
    tg_id = message.from_user.id
    tg_name = message.from_user.full_name or str(tg_id)

    async with async_session() as session:
        async with session.begin():
            user, created = await get_or_create_user(session, tg_id, tg_name)
            user_id = user.id
            traffic = user.traffic
            reputation = user.reputation

    if created:
        await message.answer(
            f"欢迎加入 商业帝国!\n"
            f"已发放初始资金: {fmt_traffic(settings.initial_traffic)}\n\n"
            f"使用下方菜单开始游戏:",
            reply_markup=main_menu_kb(),
        )
    else:
        await message.answer(
            f"🏢 商业帝国 — 主菜单",
            reply_markup=main_menu_kb(),
        )


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(HELP_TEXT)


@router.callback_query(F.data == "menu:main")
async def cb_menu_main(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🏢 商业帝国 — 主菜单",
        reply_markup=main_menu_kb(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:profile")
async def cb_menu_profile(callback: types.CallbackQuery):
    tg_id = callback.from_user.id

    async with async_session() as session:
        from services.user_service import get_user_by_tg_id
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /start 注册", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)
        traffic = user.traffic
        reputation = user.reputation

    points = await get_points(tg_id)
    quota = await get_quota_mb(tg_id)

    company_names = ", ".join(c.name for c in companies) if companies else "无"

    text = (
        f"📊 个人面板 — {callback.from_user.full_name}\n"
        f"{'─' * 24}\n"
        f"💰 金币: {fmt_traffic(traffic)}\n"
        f"⭐ 声望: {reputation}\n"
        f"🎁 积分: {points:,}\n"
        f"📦 额度: {fmt_quota(quota)}\n"
        f"🏢 公司: {company_names}\n"
    )

    await callback.message.edit_text(text, reply_markup=main_menu_kb())
    await callback.answer()


@router.callback_query(F.data == "menu:leaderboard")
async def cb_menu_leaderboard(callback: types.CallbackQuery):
    """Show leaderboard with category buttons."""
    await _show_leaderboard(callback, "revenue")


@router.callback_query(F.data.startswith("leaderboard:"))
async def cb_leaderboard_switch(callback: types.CallbackQuery):
    board_type = callback.data.split(":")[1]
    await _show_leaderboard(callback, board_type)


LEADERBOARD_TYPES = {
    "revenue": "📈 日营收",
    "funds": "💰 总资金",
    "valuation": "🏷 估值",
}


async def _show_leaderboard(callback: types.CallbackQuery, board_type: str):
    title = LEADERBOARD_TYPES.get(board_type, "排行榜")
    lb_data = await get_leaderboard(board_type, 10)

    lines = [
        f"{title} TOP 10",
        "─" * 24,
    ]
    if not lb_data:
        lines.append("暂无数据")
    else:
        for i, (name, score) in enumerate(lb_data, 1):
            medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"{i}.")
            lines.append(f"{medal} {name}: {compact_number(int(score))}")

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    # Category buttons
    cat_buttons = []
    for key, label in LEADERBOARD_TYPES.items():
        if key == board_type:
            cat_buttons.append(InlineKeyboardButton(text=f"[{label}]", callback_data=f"leaderboard:{key}"))
        else:
            cat_buttons.append(InlineKeyboardButton(text=label, callback_data=f"leaderboard:{key}"))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        cat_buttons,
        [InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")],
    ])
    try:
        await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    except Exception:
        pass
    await callback.answer()

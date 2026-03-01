"""Weekly quest handler — /company_quest command and inline panel."""

from __future__ import annotations

import logging

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from commands import CMD_QUEST
from db.engine import async_session
from services.quest_service import (
    get_or_create_weekly_tasks,
    claim_quest_reward,
    current_week_key,
    load_quests,
    get_user_titles,
)
from services.user_service import get_user_by_tg_id
from utils.panel_owner import mark_panel

from keyboards.menus import tag_kb

router = Router()
logger = logging.getLogger(__name__)


def _quest_list_kb(tasks, tg_id: int | None = None) -> InlineKeyboardMarkup:
    quests = load_quests()
    quest_map = {q["quest_id"]: q for q in quests}
    buttons = []
    for t in tasks:
        q = quest_map.get(t.quest_id, {})
        name = q.get("name", t.quest_id)
        if t.rewarded:
            buttons.append([InlineKeyboardButton(
                text=f"✅ {name} ({t.progress}/{t.target}) 已领取",
                callback_data="quest:noop",
            )])
        elif t.completed:
            buttons.append([InlineKeyboardButton(
                text=f"🎁 {name} ({t.progress}/{t.target}) 领取奖励!",
                callback_data=f"quest:claim:{t.quest_id}",
            )])
        else:
            pct = int(t.progress / t.target * 100) if t.target > 0 else 0
            buttons.append([InlineKeyboardButton(
                text=f"⬜ {name} ({t.progress}/{t.target}) {pct}%",
                callback_data=f"quest:detail:{t.quest_id}",
            )])
    buttons.append([InlineKeyboardButton(text="🔙 主菜单", callback_data="menu:main")])
    return tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)


async def _build_quest_text(user_id: int, tasks) -> str:
    week = current_week_key()
    completed = sum(1 for t in tasks if t.completed)
    titles = await get_user_titles(user_id)
    lines = [
        f"🎯 周任务清单 ({week})",
        f"{'─' * 24}",
        f"进度: {completed}/{len(tasks)} 完成",
    ]
    if titles:
        lines.append(f"🏅 称号: {', '.join(titles)}")
    return "\n".join(lines)


@router.message(Command(CMD_QUEST))
async def cmd_quest(message: types.Message):
    tg_id = message.from_user.id
    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await message.answer("请先 /company_create 创建公司")
                return
            tasks = await get_or_create_weekly_tasks(session, user.id)

    text = await _build_quest_text(user.id, tasks)
    await message.answer(text, reply_markup=_quest_list_kb(tasks, tg_id=message.from_user.id))


@router.callback_query(F.data == "menu:quest")
async def cb_quest_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            tasks = await get_or_create_weekly_tasks(session, user.id)

    text = await _build_quest_text(user.id, tasks)
    try:
        await callback.message.edit_text(text, reply_markup=_quest_list_kb(tasks, tg_id=callback.from_user.id))
    except Exception:
        await callback.message.answer(text, reply_markup=_quest_list_kb(tasks, tg_id=callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data.startswith("quest:claim:"))
async def cb_quest_claim(callback: types.CallbackQuery):
    quest_id = callback.data.split(":")[2]
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            ok, msg = await claim_quest_reward(session, user.id, quest_id)

    await callback.answer(msg, show_alert=True)
    if ok:
        # Refresh panel
        async with async_session() as session:
            async with session.begin():
                user = await get_user_by_tg_id(session, tg_id)
                tasks = await get_or_create_weekly_tasks(session, user.id)
        text = await _build_quest_text(user.id, tasks)
        try:
            await callback.message.edit_text(text, reply_markup=_quest_list_kb(tasks, tg_id=callback.from_user.id))
        except Exception:
            pass


@router.callback_query(F.data.startswith("quest:detail:"))
async def cb_quest_detail(callback: types.CallbackQuery):
    quest_id = callback.data.split(":")[2]
    quests = load_quests()
    q = next((q for q in quests if q["quest_id"] == quest_id), None)
    if not q:
        await callback.answer("任务不存在", show_alert=True)
        return

    from utils.formatters import fmt_traffic
    reward_parts = []
    if q["reward_points"]:
        reward_parts.append(f"积分+{q['reward_points']}")
    if q["reward_currency"]:
        reward_parts.append(f"+{fmt_traffic(q['reward_currency'])}")
    if q.get("reward_title"):
        reward_parts.append(f"称号「{q['reward_title']}」")

    await callback.answer(
        f"{q['name']}: {q['description']}\n奖励: {' | '.join(reward_parts)}",
        show_alert=True,
    )


@router.callback_query(F.data == "quest:noop")
async def cb_quest_noop(callback: types.CallbackQuery):
    await callback.answer()

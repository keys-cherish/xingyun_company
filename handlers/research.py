"""Research handlers (group only)."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router, types

from db.engine import async_session
from keyboards.menus import tech_list_kb
from services.company_service import get_company_by_id
from services.research_service import (
    get_available_techs,
    get_completed_techs,
    get_in_progress_research,
    get_tech_tree_display,
    start_research,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_duration

router = Router()


@router.callback_query(F.data == "menu:research")
async def cb_research_menu(callback: types.CallbackQuery):
    """Auto-select company for research if only one, otherwise show selector."""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /start 注册", show_alert=True)
            return
        from services.company_service import get_companies_by_owner
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你还没有公司", show_alert=True)
        return

    if len(companies) == 1:
        # Auto-redirect to the single company's research
        callback.data = f"research:list:{companies[0].id}"
        await cb_research_list(callback)
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"research:list:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    await callback.message.edit_text(
        "🔬 选择公司查看科研:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("research:list:"))
async def cb_research_list(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return

        completed = await get_completed_techs(session, company_id)
        in_progress = await get_in_progress_research(session, company_id)
        available = await get_available_techs(session, company_id)

    lines = [f"🔬 {company.name} — 科研中心", "─" * 24]
    if completed:
        lines.append("✅ 已完成:")
        tree = {t["tech_id"]: t for t in get_tech_tree_display()}
        for tid in completed:
            name = tree.get(tid, {}).get("name", tid)
            lines.append(f"  • {name}")

    if in_progress:
        lines.append("\n⏳ 研究中:")
        tree = {t["tech_id"]: t for t in get_tech_tree_display()}
        now = dt.datetime.utcnow()
        for rp in in_progress:
            tech_info = tree.get(rp.tech_id, {})
            name = tech_info.get("name", rp.tech_id)
            duration_sec = tech_info.get("duration_seconds", 3600)
            started = rp.started_at.replace(tzinfo=None) if rp.started_at.tzinfo else rp.started_at
            elapsed = (now - started).total_seconds()
            remaining = max(0, int(duration_sec - elapsed))
            if remaining > 0:
                lines.append(f"  • {name} — 剩余 {fmt_duration(remaining)}")
            else:
                lines.append(f"  • {name} — 即将完成（等待结算）")

    if available:
        lines.append("\n📋 可研究:")
    else:
        lines.append("\n暂无可研究项目")

    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=tech_list_kb(available, company_id))
    await callback.answer()


@router.callback_query(F.data.startswith("research:start:"))
async def cb_start_research(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    tech_id = parts[3]
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能进行科研", show_alert=True)
                return
            ok, msg = await start_research(session, company_id, user.id, tech_id)

    await callback.answer(msg, show_alert=True)

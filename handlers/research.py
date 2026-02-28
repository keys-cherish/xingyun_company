"""Research handlers (group only)."""

from __future__ import annotations

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

router = Router()


@router.callback_query(F.data == "menu:research")
async def cb_research_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🔬 科研中心\n请先选择一个公司查看科研。"
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
        for rp in in_progress:
            name = tree.get(rp.tech_id, {}).get("name", rp.tech_id)
            lines.append(f"  • {name} (开始于 {rp.started_at.strftime('%m-%d %H:%M')})")

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

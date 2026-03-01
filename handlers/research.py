"""Research handlers (group only)."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router, types
from sqlalchemy import func as sqlfunc, select

from db.engine import async_session
from keyboards.menus import tech_list_kb, tag_kb
from services.company_service import get_company_by_id
from services.research_service import (
    check_and_complete_research,
    get_effective_research_duration_seconds,
    get_available_techs,
    get_company_direction_product_lines,
    get_company_research_directions,
    get_completed_techs,
    get_in_progress_research,
    get_tech_tree_display,
    start_research,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_duration
from utils.timezone import naive_utc_to_bj

router = Router()


@router.callback_query(F.data == "menu:research")
async def cb_research_menu(callback: types.CallbackQuery):
    """Auto-select company for research if only one, otherwise show selector."""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        from services.company_service import get_companies_by_owner
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你还没有公司", show_alert=True)
        return

    if len(companies) == 1:
        # Auto-redirect to the single company's research
        await cb_research_list(callback, companies[0].id)
        return

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"research:list:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:company")])
    await callback.message.edit_text(
        "🔬 选择公司查看科研:",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("research:list:"))
async def cb_research_list(callback: types.CallbackQuery, company_id: int | None = None):
    if company_id is None:
        company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        company = await get_company_by_id(session, company_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        if not company or company.owner_id != user.id:
            await callback.answer("无权操作", show_alert=True)
            return

        completed_now = await check_and_complete_research(session, company_id)
        completed = await get_completed_techs(session, company_id)
        in_progress = await get_in_progress_research(session, company_id)
        available = await get_available_techs(session, company_id)
        now_db = (await session.execute(select(sqlfunc.now()))).scalar()
        if now_db is None:
            now_db = dt.datetime.utcnow()
        if getattr(now_db, "tzinfo", None):
            now_db = now_db.replace(tzinfo=None)

    tree = {t["tech_id"]: t for t in get_tech_tree_display()}
    lines = [f"🔬 {company.name} — 科研中心", "─" * 24]
    directions = get_company_research_directions(company.company_type)
    direction_lines = get_company_direction_product_lines(company.company_type)
    lines.append("🧭 行业研发方向:")
    for idx, direction in enumerate(directions, 1):
        lines.append(f"  {idx}. {direction['name']}")
    for direction in direction_lines:
        products = direction["product_lines"]  # type: ignore[index]
        if products:
            lines.append(f"    ↳ 产品线: {', '.join(products[:4])}")
        else:
            lines.append("    ↳ 产品线: 待解锁")
    lines.append("")
    if completed_now:
        lines.append(f"🎉 刚完成: {', '.join(completed_now)}")
        lines.append("")

    if completed:
        lines.append("✅ 已完成科技:")
        for tid in completed:
            name = tree.get(tid, {}).get("name", tid)
            lines.append(f"  • {name}")

    if in_progress:
        lines.append("")
        lines.append("⏳ 研究中:")
        now = now_db
        for rp in in_progress:
            tech_info = tree.get(rp.tech_id, {})
            name = tech_info.get("name", rp.tech_id)
            duration_sec = get_effective_research_duration_seconds(
                tech_info,
                company.company_type,
                rp.tech_id,
            )
            started = rp.started_at.replace(tzinfo=None) if rp.started_at.tzinfo else rp.started_at
            elapsed = max(0.0, (now - started).total_seconds())
            remaining = max(0, int(duration_sec - elapsed))
            # 格式化开始时间
            start_display = naive_utc_to_bj(rp.started_at).strftime("%m-%d %H:%M")
            if remaining > 0:
                lines.append(
                    f"  • {name}\n"
                    f"    状态: 研究中\n"
                    f"    开始时间(北京时间): {start_display}\n"
                    f"    所需时间: {fmt_duration(duration_sec)}\n"
                    f"    剩余时间: {fmt_duration(remaining)}"
                )
            else:
                lines.append(f"  • {name} — 已到期，将自动完成")

    lines.append("")
    if available:
        lines.append("🆕 可研究科技:")
    else:
        lines.append("暂无可研究科技")

    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=tech_list_kb(available, company_id, tg_id=callback.from_user.id))
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
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能进行科研", show_alert=True)
                return
            ok, msg = await start_research(session, company_id, user.id, tech_id)

    await callback.answer(msg, show_alert=True)

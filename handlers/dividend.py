"""Dividend record handlers (group only)."""

from __future__ import annotations

from aiogram import F, Router, types
from sqlalchemy import select

from db.engine import async_session
from db.models import DailyReport
from keyboards.menus import main_menu_kb
from services.company_service import get_companies_by_owner, get_company_by_id
from services.settlement_service import format_daily_report
from services.user_service import get_user_by_tg_id

router = Router()


@router.callback_query(F.data == "menu:dividend")
async def cb_dividend_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /create_company 创建公司", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

        if not companies:
            await callback.message.edit_text("你还没有公司。", reply_markup=main_menu_kb())
            await callback.answer()
            return

        lines = ["💰 最近分红/结算记录", "─" * 24]
        for company in companies:
            result = await session.execute(
                select(DailyReport)
                .where(DailyReport.company_id == company.id)
                .order_by(DailyReport.id.desc())
                .limit(3)
            )
            reports = result.scalars().all()
            if reports:
                for r in reports:
                    lines.append(format_daily_report(company, r))
                    lines.append("")
            else:
                lines.append(f"「{company.name}」暂无结算记录")

    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_kb())
    await callback.answer()

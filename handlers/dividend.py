"""Dividend handlers: manual distribution and history."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.engine import async_session
from db.models import DailyReport
from keyboards.menus import tag_kb
from services.company_service import add_funds, get_company_by_id
from services.shareholder_service import get_shareholders
from services.user_service import add_traffic, get_user_by_tg_id
from utils.formatters import fmt_shares, fmt_traffic

router = Router()


def _dividend_amount_kb(company_id: int, tg_id: int) -> InlineKeyboardMarkup:
    """分红金额选择键盘。"""
    amounts = [1000, 5000, 10000, 50000]
    buttons = [
        [InlineKeyboardButton(
            text=f"💸 分红 {fmt_traffic(a)}",
            callback_data=f"dividend:confirm:{company_id}:{a}",
        )]
        for a in amounts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回股东", callback_data=f"shareholder:list:{company_id}")])
    return tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)


@router.callback_query(F.data.startswith("dividend:distribute:"))
async def cb_dividend_distribute(callback: types.CallbackQuery):
    """显示分红金额选择界面。"""
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        company = await get_company_by_id(session, company_id)
        if not user or not company:
            await callback.answer("数据异常", show_alert=True)
            return
        if company.owner_id != user.id:
            await callback.answer("只有老板才能发放分红", show_alert=True)
            return

        shareholders = await get_shareholders(session, company_id)
        if not shareholders:
            await callback.answer("暂无股东，无法分红", show_alert=True)
            return

        lines = [
            "💸 发放分红",
            "─" * 24,
            f"🏢 公司: {company.name}",
            f"🏦 公司积分: {fmt_traffic(company.total_funds)}",
            "",
            "📊 股东持股比例:",
        ]
        for sh in shareholders:
            from db.models import User
            u = await session.get(User, sh.user_id)
            name = u.tg_name if u else "未知"
            lines.append(f"  • {name}: {fmt_shares(sh.shares)}")
        lines.append("")
        lines.append("选择分红总金额（将按股份比例分配）:")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_dividend_amount_kb(company_id, tg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("dividend:confirm:"))
async def cb_dividend_confirm(callback: types.CallbackQuery):
    """显示分红确认界面。"""
    parts = callback.data.split(":")
    company_id = int(parts[2])
    amount = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        company = await get_company_by_id(session, company_id)
        if not user or not company:
            await callback.answer("数据异常", show_alert=True)
            return
        if company.owner_id != user.id:
            await callback.answer("只有老板才能发放分红", show_alert=True)
            return
        if company.total_funds < amount:
            await callback.answer(f"公司积分不足，当前: {fmt_traffic(company.total_funds)}", show_alert=True)
            return

        shareholders = await get_shareholders(session, company_id)
        if not shareholders:
            await callback.answer("暂无股东", show_alert=True)
            return

        lines = [
            "💸 分红确认",
            "─" * 24,
            f"分红总额: {fmt_traffic(amount)}",
            "",
            "📊 各股东将获得:",
        ]
        for sh in shareholders:
            from db.models import User
            u = await session.get(User, sh.user_id)
            name = u.tg_name if u else "未知"
            share_amount = int(amount * sh.shares / 100)
            lines.append(f"  • {name} ({fmt_shares(sh.shares)}): +{fmt_traffic(share_amount)}")
        lines.append("")
        lines.append(f"公司积分: {fmt_traffic(company.total_funds)} → {fmt_traffic(company.total_funds - amount)}")

    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ 确认发放", callback_data=f"dividend:execute:{company_id}:{amount}"),
            InlineKeyboardButton(text="🔙 取消", callback_data=f"dividend:distribute:{company_id}"),
        ],
    ]), tg_id)

    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("dividend:execute:"))
async def cb_dividend_execute(callback: types.CallbackQuery):
    """执行分红操作。"""
    parts = callback.data.split(":")
    company_id = int(parts[2])
    amount = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not user or not company:
                await callback.answer("数据异常", show_alert=True)
                return
            if company.owner_id != user.id:
                await callback.answer("只有老板才能发放分红", show_alert=True)
                return
            if company.total_funds < amount:
                await callback.answer(f"公司积分不足", show_alert=True)
                return

            shareholders = await get_shareholders(session, company_id)
            if not shareholders:
                await callback.answer("暂无股东", show_alert=True)
                return

            # 先扣除公司积分
            ok = await add_funds(session, company_id, -amount)
            if not ok:
                await callback.answer("扣款失败，请重试", show_alert=True)
                return

            # 按股份比例分配给各股东
            distributions = []
            failed_total = 0
            for sh in shareholders:
                share_amount = int(amount * sh.shares / 100)
                if share_amount > 0:
                    success = await add_traffic(session, sh.user_id, share_amount)
                    if success:
                        from db.models import User
                        u = await session.get(User, sh.user_id)
                        name = u.tg_name if u else "未知"
                        distributions.append((name, sh.shares, share_amount))
                    else:
                        failed_total += share_amount

            # 退还失败的部分
            if failed_total > 0:
                await add_funds(session, company_id, failed_total)

    # 构建结果消息
    total_distributed = sum(d[2] for d in distributions)
    lines = [
        "✅ 分红发放成功!",
        "─" * 24,
        f"💸 分红总额: {fmt_traffic(total_distributed)}",
        "",
        "📊 分配详情:",
    ]
    for name, shares, amt in distributions:
        lines.append(f"  • {name} ({fmt_shares(shares)}): +{fmt_traffic(amt)}")

    if failed_total > 0:
        lines.append(f"\n⚠️ 部分分红失败，已退还: {fmt_traffic(failed_total)}")

    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回股东列表", callback_data=f"shareholder:list:{company_id}")],
    ]), tg_id)

    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer("分红发放成功!", show_alert=True)


@router.callback_query(F.data.startswith("dividend:history:"))
async def cb_dividend_history(callback: types.CallbackQuery):
    """显示公司分红/结算历史记录。"""
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return

        result = await session.execute(
            select(DailyReport)
            .where(DailyReport.company_id == company_id)
            .order_by(DailyReport.id.desc())
            .limit(5)
        )
        reports = result.scalars().all()

        lines = [f"📜 {company.name} — 分红记录", "─" * 24]
        if reports:
            for r in reports:
                profit = r.total_income - r.operating_cost
                lines.append(f"📅 {r.date}")
                lines.append(f"  收入: {fmt_traffic(r.total_income)}")
                lines.append(f"  成本: -{fmt_traffic(r.operating_cost)}")
                lines.append(f"  利润: {fmt_traffic(profit)}")
                lines.append(f"  分红: {fmt_traffic(r.dividend_paid)}")
                lines.append("")
        else:
            lines.append("暂无结算记录")

    kb = tag_kb(InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 返回股东列表", callback_data=f"shareholder:list:{company_id}")],
    ]), tg_id)

    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# 保留旧的主菜单入口（兼容）
@router.callback_query(F.data == "menu:dividend")
async def cb_dividend_menu(callback: types.CallbackQuery):
    """主菜单分红入口 - 显示所有公司的结算记录。"""
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        from services.company_service import get_companies_by_owner
        companies = await get_companies_by_owner(session, user.id)

        if not companies:
            from keyboards.menus import main_menu_kb
            await callback.message.edit_text("你还没有公司。", reply_markup=main_menu_kb(tg_id=tg_id))
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
                from services.settlement_service import format_daily_report
                for r in reports:
                    lines.append(format_daily_report(company, r))
                    lines.append("")
            else:
                lines.append(f"「{company.name}」暂无结算记录")

    from keyboards.menus import main_menu_kb
    await callback.message.edit_text("\n".join(lines), reply_markup=main_menu_kb(tg_id=tg_id))
    await callback.answer()

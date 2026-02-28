"""广告处理器（仅群组）。"""

from __future__ import annotations

from aiogram import F, Router, types

from db.engine import async_session
from keyboards.menus import main_menu_kb
from services.ad_service import get_active_ad_info, get_ad_tiers, buy_ad, cancel_ad
from services.company_service import add_funds, get_company_by_id
from services.user_service import get_user_by_tg_id
from handlers.company import _refresh_company_view

router = Router()


def _ad_menu_kb(company_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    tiers = get_ad_tiers()
    buttons = [
        [InlineKeyboardButton(
            text=f"{t['name']} ({t['cost']}💰 {t['description']})",
            callback_data=f"ad:buy:{company_id}:{t['key']}",
        )]
        for t in tiers
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(F.data.startswith("ad:menu:"))
async def cb_ad_menu(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        company = await get_company_by_id(session, company_id)
        if not user:
            await callback.answer("请先 /create_company 创建公司", show_alert=True)
            return
        if not company or company.owner_id != user.id:
            await callback.answer("无权操作", show_alert=True)
            return

    ad_info = await get_active_ad_info(company_id)
    if ad_info:
        text = (
            f"📢 广告投放\n"
            f"当前活动: {ad_info.get('name', '广告')}\n"
            f"营收加成: +{ad_info['boost_pct']*100:.0f}%\n"
            f"剩余天数: {ad_info['remaining_days']}天\n\n"
            "当前已有活动广告，请等待结束后购买新广告。"
        )
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")]
        ])
        await callback.message.edit_text(text, reply_markup=kb)
    else:
        await callback.message.edit_text("📢 选择广告方案:", reply_markup=_ad_menu_kb(company_id))
    await callback.answer()


@router.callback_query(F.data.startswith("ad:buy:"))
async def cb_buy_ad(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    tier_key = parts[3]
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not company or not user or company.owner_id != user.id:
                await callback.answer("只有公司老板才能购买广告", show_alert=True)
                return

            # 先检查资金是否足够，再购买广告（原子性保证）
            from services.ad_service import AD_TIERS
            tier = next((t for t in AD_TIERS if t["key"] == tier_key), None)
            if not tier:
                await callback.answer("无效的广告类型", show_alert=True)
                return

            cost = tier["cost"]
            fund_ok = await add_funds(session, company_id, -cost)
            if not fund_ok:
                await callback.answer(f"公司资金不足，需要 {cost:,} 金币", show_alert=True)
                return

            # 资金扣除成功后再购买广告
            ok, msg, _ = await buy_ad(company_id, tier_key)
            if not ok:
                # 回滚资金
                await add_funds(session, company_id, cost)
                await callback.answer(msg, show_alert=True)
                return

    await callback.answer(msg, show_alert=True)
    await _refresh_company_view(callback, company_id)

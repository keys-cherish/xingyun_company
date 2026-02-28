"""产品处理器（仅群组）。支持创建、升级、下架/删除产品。"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from keyboards.menus import product_detail_kb, product_template_kb
from services.company_service import get_company_by_id
from services.product_service import (
    create_product,
    get_available_product_templates,
    get_company_products,
    upgrade_product,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()


async def _refresh_product_list(callback: types.CallbackQuery, company_id: int):
    """操作后刷新产品列表消息。"""
    try:
        async with async_session() as session:
            company = await get_company_by_id(session, company_id)
            if not company:
                return
            products = await get_company_products(session, company_id)
            templates = await get_available_product_templates(session, company_id)

        lines = [f"📦 {company.name} — 产品列表", "─" * 24]

        product_buttons = []
        if products:
            for p in products:
                lines.append(f"• {p.name} v{p.version} — {fmt_traffic(p.daily_income)}/日 (品质:{p.quality})")
                product_buttons.append([
                    InlineKeyboardButton(text=f"⬆️x1 {p.name}", callback_data=f"product:upgrade:{p.id}:1"),
                    InlineKeyboardButton(text=f"⬆️x5 {p.name}", callback_data=f"product:upgrade:{p.id}:5"),
                    InlineKeyboardButton(text=f"🗑 下架", callback_data=f"product:delete:{p.id}:{company_id}"),
                ])
        else:
            lines.append("暂无产品")

        lines.append("\n🆕 可创建的产品:")
        text = "\n".join(lines)

        template_buttons = [
            [InlineKeyboardButton(
                text=f"{t['name']} (💰{t['base_daily_income']}/日)",
                callback_data=f"product:create:{company_id}:{t['product_key']}",
            )]
            for t in templates
        ]
        all_buttons = product_buttons + template_buttons
        all_buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
        kb = InlineKeyboardMarkup(inline_keyboard=all_buttons)

        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass  # 消息未变化时edit会抛异常，忽略


@router.callback_query(F.data == "menu:product")
async def cb_product_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("📦 产品管理\n请先从公司面板进入产品列表。")
    await callback.answer()


@router.callback_query(F.data.startswith("product:list:"))
async def cb_product_list(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return
        products = await get_company_products(session, company_id)
        templates = await get_available_product_templates(session, company_id)

    lines = [f"📦 {company.name} — 产品列表", "─" * 24]

    # 为每个产品生成详情按钮
    product_buttons = []
    if products:
        for p in products:
            lines.append(f"• {p.name} v{p.version} — {fmt_traffic(p.daily_income)}/日 (品质:{p.quality})")
            product_buttons.append([
                InlineKeyboardButton(text=f"⬆️x1 {p.name}", callback_data=f"product:upgrade:{p.id}:1"),
                InlineKeyboardButton(text=f"⬆️x5 {p.name}", callback_data=f"product:upgrade:{p.id}:5"),
                InlineKeyboardButton(text=f"🗑 下架", callback_data=f"product:delete:{p.id}:{company_id}"),
            ])
    else:
        lines.append("暂无产品")

    lines.append("\n🆕 可创建的产品:")
    text = "\n".join(lines)

    # 合并产品操作按钮和模板按钮
    template_buttons = [
        [InlineKeyboardButton(
            text=f"{t['name']} (💰{t['base_daily_income']}/日)",
            callback_data=f"product:create:{company_id}:{t['product_key']}",
        )]
        for t in templates
    ]
    all_buttons = product_buttons + template_buttons
    all_buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=all_buttons)

    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("product:create:"))
async def cb_create_product(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    product_key = parts[3]
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能创建产品", show_alert=True)
                return
            product, msg = await create_product(session, company_id, user.id, product_key)

    await callback.answer(msg, show_alert=True)
    if product:
        await _refresh_product_list(callback, company_id)


@router.callback_query(F.data.startswith("product:upgrade:"))
async def cb_upgrade_product(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    product_id = int(parts[2])
    count = int(parts[3]) if len(parts) > 3 else 1
    tg_id = callback.from_user.id

    upgraded = 0
    last_msg = ""

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            for i in range(count):
                ok, msg = await upgrade_product(session, product_id, user.id)
                if not ok:
                    if upgraded == 0:
                        # First attempt failed, show original error
                        await callback.answer(msg, show_alert=True)
                        return
                    else:
                        # Some succeeded, break and report partial success
                        last_msg = msg
                        break
                upgraded += 1
                last_msg = msg
            # Get final product state for the summary
            from db.models import Product as ProductModel
            product = await session.get(ProductModel, product_id)

    if upgraded == 1:
        await callback.answer(last_msg, show_alert=True)
    else:
        await callback.answer(
            f"产品「{product.name}」连续升级{upgraded}次! "
            f"当前v{product.version}，日收入: {product.daily_income}MB",
            show_alert=True,
        )
    await _refresh_product_list(callback, product.company_id)


@router.callback_query(F.data.startswith("product:delete:"))
async def cb_delete_product(callback: types.CallbackQuery):
    """下架/删除产品。"""
    parts = callback.data.split(":")
    product_id = int(parts[2])
    company_id = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await callback.answer("请先 /start 注册", show_alert=True)
                return
            company = await get_company_by_id(session, company_id)
            if not company or company.owner_id != user.id:
                await callback.answer("只有公司老板才能下架产品", show_alert=True)
                return
            from db.models import Product
            product = await session.get(Product, product_id)
            if not product or product.company_id != company_id:
                await callback.answer("产品不存在", show_alert=True)
                return
            name = product.name
            await session.delete(product)

    await callback.answer(f"产品「{name}」已下架", show_alert=True)
    await _refresh_product_list(callback, company_id)

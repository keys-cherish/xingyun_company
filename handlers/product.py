"""产品处理器。支持创建、升级、下架/删除产品。"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from keyboards.menus import product_detail_kb, product_template_kb
from services.company_service import get_company_by_id, get_companies_by_owner, update_daily_revenue, add_funds
from services.product_service import (
    create_product,
    get_available_product_templates,
    get_company_products,
    upgrade_product,
)
from services.user_service import get_user_by_tg_id, add_points
from utils.formatters import fmt_traffic
from db.models import Product as ProductModel

router = Router()

# /new_product 参数：投入资金 -> 基础日收入的转化率
INVEST_TO_INCOME_RATE = 0.03  # 每投入100金币 = 3金币/日
EMPLOYEE_INCOME_BONUS = 0.10  # 每分配1名员工 +10% 收入
PERFECT_QUALITY_THRESHOLD = 100  # 完美品质阈值
PERFECT_QUALITY_BONUS = 1.0     # 完美品质额外+100%收入


@router.message(Command("new_product"))
async def cmd_new_product(message: types.Message):
    """Create a custom product: /new_product <name> <investment> <employees>."""
    tg_id = message.from_user.id
    args = (message.text or "").split()

    if len(args) < 4:
        await message.answer(
            "📦 用法: /new_product <产品名> <投入资金> <分配人员>\n"
            "例: /new_product 智能助手 10000 3\n\n"
            "• 投入资金从公司扣除，决定产品基础日收入\n"
            "• 分配人员提供额外收入加成（每人+10%）\n"
            "• 分配的人员不会减少公司员工数"
        )
        return

    product_name = args[1]
    try:
        investment = int(args[2])
        employees = int(args[3])
    except ValueError:
        await message.answer("❌ 资金和人员必须是数字")
        return

    if investment < 1000:
        await message.answer("❌ 最低投入 1,000 金币")
        return
    if investment > 500000:
        await message.answer("❌ 单次最高投入 500,000 金币")
        return
    if employees < 0 or employees > 50:
        await message.answer("❌ 分配人员数量 0-50")
        return
    if len(product_name) > 32:
        await message.answer("❌ 产品名称最长32字符")
        return

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await message.answer("请先 /start 注册")
                return
            companies = await get_companies_by_owner(session, user.id)
            if not companies:
                await message.answer("你还没有公司")
                return
            company = companies[0]

            # 计算已分配员工数
            from sqlalchemy import select, func as sqlfunc
            assigned_total = (await session.execute(
                select(sqlfunc.coalesce(sqlfunc.sum(ProductModel.assigned_employees), 0))
                .where(ProductModel.company_id == company.id)
            )).scalar() or 0
            available_employees = company.employee_count - assigned_total

            if employees > available_employees:
                await message.answer(
                    f"❌ 可用员工不足\n"
                    f"总员工: {company.employee_count} | 已分配: {assigned_total} | 可用: {available_employees}"
                )
                return

            # Deduct investment from company funds
            ok = await add_funds(session, company.id, -investment)
            if not ok:
                await message.answer(f"❌ 公司资金不足，需要 {fmt_traffic(investment)}")
                return

            # Check duplicate name
            existing = await session.execute(
                select(ProductModel).where(
                    ProductModel.company_id == company.id,
                    ProductModel.name == product_name,
                )
            )
            if existing.scalar_one_or_none():
                await add_funds(session, company.id, investment)
                await message.answer(f"❌ 已存在同名产品「{product_name}」")
                return

            # Calculate daily income with randomness
            import random
            base_income = int(investment * INVEST_TO_INCOME_RATE)
            # Random factor: ±30% on base income
            income_luck = random.uniform(0.70, 1.30)
            base_income = max(1, int(base_income * income_luck))
            employee_bonus = int(base_income * EMPLOYEE_INCOME_BONUS * employees)
            daily_income = base_income + employee_bonus

            # Quality: base from employees + heavy randomness
            # Base: 5~30 from employees, random: ±20, very rare to hit 100
            base_quality = min(5 + employees * 2, 40)
            quality_roll = random.gauss(base_quality, 15)  # Normal distribution
            quality = max(1, min(100, int(quality_roll)))

            # Perfect quality (100) is extremely rare
            # Check if company already has a perfect product (max 1 per company)
            if quality >= PERFECT_QUALITY_THRESHOLD:
                from sqlalchemy import select as sql_select
                existing_perfect = (await session.execute(
                    sql_select(sqlfunc.count()).where(
                        ProductModel.company_id == company.id,
                        ProductModel.quality >= PERFECT_QUALITY_THRESHOLD,
                    )
                )).scalar() or 0
                if existing_perfect > 0:
                    quality = 99  # Downgrade, company already has a perfect product

            # Perfect quality doubles income permanently
            perfect_msg = ""
            if quality >= PERFECT_QUALITY_THRESHOLD:
                daily_income = int(daily_income * (1 + PERFECT_QUALITY_BONUS))
                perfect_msg = "\n\n🌟 完美品质! 日收入永久翻倍!\n🏅 获得称号「万中无一」"

            product = ProductModel(
                company_id=company.id,
                name=product_name,
                tech_id="custom",
                daily_income=daily_income,
                quality=quality,
                assigned_employees=employees,
            )
            session.add(product)
            await update_daily_revenue(session, company.id)
            await add_points(user.id, 10, session=session)

    await message.answer(
        f"📦 产品「{product_name}」研发成功!\n"
        f"{'─' * 24}\n"
        f"投入资金: {fmt_traffic(investment)}\n"
        f"分配人员: {employees} 人\n"
        f"基础日收入: {fmt_traffic(base_income)}\n"
        f"人员加成: +{fmt_traffic(employee_bonus)}\n"
        f"总日收入: {fmt_traffic(daily_income)}\n"
        f"产品品质: {quality}/100"
        f"{perfect_msg}"
    )


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
    """Auto-select company for products if only one, otherwise show selector."""
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
        callback.data = f"product:list:{companies[0].id}"
        await cb_product_list(callback)
        return

    buttons = [
        [InlineKeyboardButton(text=c.name, callback_data=f"product:list:{c.id}")]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    await callback.message.edit_text(
        "📦 选择公司查看产品:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
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
            if product:
                await update_daily_revenue(session, company_id)

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
            await update_daily_revenue(session, product.company_id)

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
            await update_daily_revenue(session, company_id)

    await callback.answer(f"产品「{name}」已下架", show_alert=True)
    await _refresh_product_list(callback, company_id)


# ---- /clear_product 管理员命令（限定 tg_id） ----

CLEAR_PRODUCT_ADMIN_ID = 5222591634


@router.message(Command("clear_product"))
async def cmd_clear_product(message: types.Message):
    """管理员命令：回复某人消息，清除该用户所有产品。"""
    if message.from_user.id != CLEAR_PRODUCT_ADMIN_ID:
        await message.answer("❌ 无权使用此命令")
        return

    if not message.reply_to_message:
        await message.answer("用法: 回复某人消息并发送 /clear_product")
        return

    target = message.reply_to_message.from_user
    if not target:
        await message.answer("❌ 无法获取目标用户")
        return

    from sqlalchemy import select, delete
    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, target.id)
            if not user:
                await message.answer("❌ 该用户未注册")
                return
            companies = await get_companies_by_owner(session, user.id)
            if not companies:
                await message.answer("❌ 该用户没有公司")
                return

            total_deleted = 0
            for company in companies:
                result = await session.execute(
                    delete(ProductModel).where(ProductModel.company_id == company.id)
                )
                total_deleted += result.rowcount
                await update_daily_revenue(session, company.id)

    await message.answer(
        f"✅ 已清除 {target.full_name} 的所有产品 (共 {total_deleted} 个)"
    )

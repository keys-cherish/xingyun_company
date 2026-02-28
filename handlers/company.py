"""公司相关处理器。/company 在私聊和群组中均可使用。"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings as cfg
from db.engine import async_session
from keyboards.menus import company_detail_kb, company_list_kb
from services.company_service import (
    add_funds,
    create_company,
    get_companies_by_owner,
    get_company_by_id,
    get_company_type_info,
    get_company_valuation,
    get_level_info,
    get_level_employee_bonus,
    get_level_revenue_bonus,
    get_max_level,
    load_company_types,
    upgrade_company,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()


# ---- /list_company 列出所有公司 ----

@router.message(Command("list_company"))
async def cmd_list_company(message: types.Message):
    """列出服务器上所有公司。"""
    from sqlalchemy import select
    from db.models import Company, User

    async with async_session() as session:
        result = await session.execute(
            select(Company).order_by(Company.total_funds.desc())
        )
        companies = list(result.scalars().all())

    if not companies:
        await message.answer("目前还没有任何公司")
        return

    lines = [f"🏢 全服公司列表 (共 {len(companies)} 家)", f"{'─' * 28}"]
    for i, c in enumerate(companies, 1):
        type_info = get_company_type_info(c.company_type)
        emoji = type_info["emoji"] if type_info else "🏢"
        lines.append(
            f"{i}. {emoji} {c.name} (ID:{c.id})\n"
            f"   Lv.{c.level} | 资金:{fmt_traffic(c.total_funds)} | "
            f"日营收:{fmt_traffic(c.daily_revenue)} | 👷{c.employee_count}人"
        )

    await message.answer("\n".join(lines))


class CreateCompanyState(StatesGroup):
    waiting_type = State()
    waiting_name = State()


class RenameCompanyState(StatesGroup):
    waiting_new_name = State()


# ---- /member 命令：招聘/裁员 ----

@router.message(Command("member"))
async def cmd_member(message: types.Message):
    """Handle /member add|minus <count>."""
    tg_id = message.from_user.id
    args = (message.text or "").split()

    if len(args) < 3:
        await message.answer(
            "👷 员工管理:\n"
            "  /member add <数量> — 招聘员工\n"
            "  /member add max — 招满\n"
            "  /member minus <数量> — 裁员\n"
            "例: /member add 5"
        )
        return

    action = args[1].lower()
    count_str = args[2].strip()

    if action not in ("add", "minus"):
        await message.answer("❌ 操作只能是 add 或 minus")
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

            type_info = get_company_type_info(company.company_type)
            max_emp = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1) + get_level_employee_bonus(company.level)
            if type_info and type_info.get("extra_employee_limit"):
                max_emp += type_info["extra_employee_limit"]

            if action == "add":
                available_slots = max_emp - company.employee_count
                if available_slots <= 0:
                    await message.answer(f"❌ 已达员工上限 ({max_emp}人)，升级公司可提升上限")
                    return

                if count_str == "max":
                    hire_count = available_slots
                else:
                    try:
                        hire_count = int(count_str)
                    except ValueError:
                        await message.answer("❌ 数量必须是数字或 max")
                        return

                hire_count = min(hire_count, available_slots)
                if hire_count <= 0:
                    await message.answer("❌ 无可用名额")
                    return

                hire_cost_per = cfg.employee_salary_base * 10
                total_cost = hire_count * hire_cost_per

                ok = await add_funds(session, company.id, -total_cost)
                if not ok:
                    affordable = company.total_funds // hire_cost_per
                    if affordable <= 0:
                        await message.answer(f"❌ 公司资金不足，每人招聘需要 {fmt_traffic(hire_cost_per)}")
                        return
                    hire_count = min(hire_count, affordable)
                    total_cost = hire_count * hire_cost_per
                    ok = await add_funds(session, company.id, -total_cost)
                    if not ok:
                        await message.answer("❌ 公司资金不足")
                        return

                company.employee_count += hire_count
                await message.answer(
                    f"✅ 招聘成功! 招了 {hire_count} 人\n"
                    f"花费: {fmt_traffic(total_cost)}\n"
                    f"当前员工: {company.employee_count}/{max_emp}"
                )

            else:  # minus
                try:
                    fire_count = int(count_str)
                except ValueError:
                    await message.answer("❌ 数量必须是数字")
                    return

                if company.employee_count <= 1:
                    await message.answer("❌ 至少需要保留1名员工")
                    return

                max_fireable = company.employee_count - 1
                fire_count = min(fire_count, max_fireable)
                if fire_count <= 0:
                    await message.answer("❌ 至少需要保留1名员工")
                    return

                company.employee_count -= fire_count
                await message.answer(
                    f"✅ 裁员完成! 裁了 {fire_count} 人\n"
                    f"当前员工: {company.employee_count}/{max_emp}"
                )


# ---- 公共：渲染公司面板（供多处复用） ----

async def render_company_detail(company_id: int, tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """加载公司数据并返回 (text, keyboard)，供多个handler复用。"""
    from db.models import Shareholder, Product, ResearchProgress
    from sqlalchemy import select, func as sqlfunc
    from services.realestate_service import get_total_estate_income

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            return "公司不存在", InlineKeyboardMarkup(inline_keyboard=[])
        user = await get_user_by_tg_id(session, tg_id)
        valuation = await get_company_valuation(session, company)
        is_owner = user and company.owner_id == user.id

        sh_count = (await session.execute(
            select(sqlfunc.count()).where(Shareholder.company_id == company_id)
        )).scalar()
        prod_count = (await session.execute(
            select(sqlfunc.count()).where(Product.company_id == company_id)
        )).scalar()
        tech_count = (await session.execute(
            select(sqlfunc.count()).where(
                ResearchProgress.company_id == company_id,
                ResearchProgress.status == "completed",
            )
        )).scalar()
        estate_income = await get_total_estate_income(session, company_id)

    type_info = get_company_type_info(company.company_type)
    type_display = f"{type_info['emoji']} {type_info['name']}" if type_info else company.company_type

    level_info = get_level_info(company.level)
    level_name = level_info["name"] if level_info else f"Lv.{company.level}"
    level_rev_bonus = get_level_revenue_bonus(company.level)
    level_emp_bonus = get_level_employee_bonus(company.level)

    max_employees = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1) + level_emp_bonus
    if type_info and type_info.get("extra_employee_limit"):
        max_employees += type_info["extra_employee_limit"]

    total_daily = company.daily_revenue + estate_income + level_rev_bonus

    # Upgrade requirements
    next_level = company.level + 1
    next_info = get_level_info(next_level)
    if next_info:
        def _icon(current, required):
            return "✅" if current >= required else "❌"

        req_lines = [f"📤 升级 Lv.{next_level}「{next_info['name']}」条件:"]
        req_cost = next_info["upgrade_cost"]
        req_emp = next_info.get("min_employees", 0)
        req_prod = next_info.get("min_products", 0)
        req_tech = next_info.get("min_techs", 0)
        req_rev = next_info.get("min_daily_revenue", 0)

        req_lines.append(f"  {_icon(company.total_funds, req_cost)} 资金 {fmt_traffic(req_cost)}")
        if req_emp:
            req_lines.append(f"  {_icon(company.employee_count, req_emp)} 员工 ≥{req_emp}")
        if req_prod:
            req_lines.append(f"  {_icon(prod_count, req_prod)} 产品 ≥{req_prod}")
        if req_tech:
            req_lines.append(f"  {_icon(tech_count, req_tech)} 科技 ≥{req_tech}")
        if req_rev:
            req_lines.append(f"  {_icon(company.daily_revenue, req_rev)} 日营收 ≥{fmt_traffic(req_rev)}")

        upgrade_block = "\n".join(req_lines) + "\n"
    else:
        upgrade_block = "🏆 已达最高等级!\n"

    text = (
        f"🏢 {company.name} (ID: {company.id})\n"
        f"类型: {type_display}\n"
        f"{'─' * 24}\n"
        f"💰 资金: {fmt_traffic(company.total_funds)}\n"
        f"📈 日营收: {fmt_traffic(company.daily_revenue)}\n"
        f"🏗 地产收入: {fmt_traffic(estate_income)}\n"
        f"🎖 等级加成: +{fmt_traffic(level_rev_bonus)}\n"
        f"📊 日总收入: {fmt_traffic(total_daily)}\n"
        f"🏷 估值: {fmt_traffic(valuation)}\n"
        f"⭐ Lv.{company.level}「{level_name}」\n"
        f"👥 股东:{sh_count} | 👷 员工:{company.employee_count}/{max_employees} | 📦 产品:{prod_count} | 🔬 科技:{tech_count}\n"
        f"{'─' * 24}\n"
        f"{upgrade_block}"
    )
    return text, company_detail_kb(company_id, is_owner)


async def _refresh_company_view(callback: types.CallbackQuery, company_id: int):
    """操作后刷新公司面板消息。"""
    text, kb = await render_company_detail(company_id, callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass  # 消息未变化时edit会抛异常，忽略


# /company - 私聊和群组均可使用
@router.message(Command("company"))
async def cmd_company(message: types.Message):
    tg_id = message.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await message.answer("请先使用 /start 注册")
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await message.answer(
            "你还没有公司。",
            reply_markup=company_list_kb([]),
        )
        return

    # 只有一家公司时直接打开详情
    if len(companies) == 1:
        text, kb = await render_company_detail(companies[0].id, tg_id)
        await message.answer(text, reply_markup=kb)
        return

    items = [(c.id, c.name) for c in companies]
    await message.answer("🏢 你的公司列表:", reply_markup=company_list_kb(items))


@router.callback_query(F.data == "menu:company")
async def cb_menu_company(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /start 注册", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    # 只有一家公司时直接打开详情
    if len(companies) == 1:
        text, kb = await render_company_detail(companies[0].id, tg_id)
        await callback.message.edit_text(text, reply_markup=kb)
        await callback.answer()
        return

    items = [(c.id, c.name) for c in companies]
    await callback.message.edit_text("🏢 你的公司列表:", reply_markup=company_list_kb(items))
    await callback.answer()


@router.callback_query(F.data.startswith("company:view:"))
async def cb_company_view(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    text, kb = await render_company_detail(company_id, callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


# ---- 创建公司：先选类型再输入名称 ----

@router.callback_query(F.data == "company:create")
async def cb_company_create(callback: types.CallbackQuery, state: FSMContext):
    types_data = load_company_types()
    buttons = [
        [InlineKeyboardButton(
            text=f"{info['emoji']} {info['name']}",
            callback_data=f"company:type:{key}",
        )]
        for key, info in types_data.items()
    ]
    buttons.append([InlineKeyboardButton(text="🔙 取消", callback_data="menu:main")])

    await callback.message.edit_text(
        "选择公司类型:\n\n" +
        "\n".join(f"{info['emoji']} {info['name']} — {info['description']}" for info in types_data.values()),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(CreateCompanyState.waiting_type)
    await callback.answer()


@router.callback_query(F.data.startswith("company:type:"))
async def cb_company_type_selected(callback: types.CallbackQuery, state: FSMContext):
    company_type = callback.data.split(":")[2]
    await state.update_data(company_type=company_type)
    await state.set_state(CreateCompanyState.waiting_name)
    type_info = get_company_type_info(company_type)
    name = type_info["name"] if type_info else company_type
    await callback.message.edit_text(f"已选择: {name}\n\n请输入新公司名称 (2-16字):")
    await callback.answer()


@router.message(CreateCompanyState.waiting_name)
async def on_company_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not (2 <= len(name) <= 16):
        await message.answer("公司名称需要2-16个字符，请重新输入:")
        return

    data = await state.get_data()
    company_type = data.get("company_type", "tech")
    tg_id = message.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            if not user:
                await message.answer("请先 /start 注册")
                await state.clear()
                return
            company, msg = await create_company(session, user, name, company_type)

    await message.answer(msg)
    await state.clear()

    if company:
        from keyboards.menus import start_existing_user_kb
        await message.answer("返回主菜单:", reply_markup=start_existing_user_kb())


# ---- 招聘/裁员 ----

@router.callback_query(F.data.startswith("company:hire:"))
async def cb_hire(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    count_str = parts[3] if len(parts) > 3 else "1"
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not company or not user or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            type_info = get_company_type_info(company.company_type)
            max_emp = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1) + get_level_employee_bonus(company.level)
            if type_info and type_info.get("extra_employee_limit"):
                max_emp += type_info["extra_employee_limit"]
            if company.employee_count >= max_emp:
                await callback.answer(f"已达员工上限 ({max_emp}人)，升级公司可提升上限", show_alert=True)
                return

            available_slots = max_emp - company.employee_count
            if count_str == "max":
                desired = available_slots
            else:
                desired = int(count_str)
            hire_count = min(desired, available_slots)
            if hire_count <= 0:
                await callback.answer("无可用名额", show_alert=True)
                return

            hire_cost_per = cfg.employee_salary_base * 10
            total_cost = hire_count * hire_cost_per

            ok = await add_funds(session, company_id, -total_cost)
            if not ok:
                if hire_count > 1:
                    affordable = company.total_funds // hire_cost_per
                    if affordable <= 0:
                        await callback.answer(f"公司资金不足，每人招聘需要 {fmt_traffic(hire_cost_per)}", show_alert=True)
                        return
                    hire_count = min(hire_count, affordable)
                    total_cost = hire_count * hire_cost_per
                    ok = await add_funds(session, company_id, -total_cost)
                    if not ok:
                        await callback.answer(f"公司资金不足", show_alert=True)
                        return
                else:
                    await callback.answer(f"公司资金不足，招聘需要 {fmt_traffic(hire_cost_per)}", show_alert=True)
                    return
            company.employee_count += hire_count

    await callback.answer(
        f"招聘成功! 招了{hire_count}人，花费 {fmt_traffic(total_cost)}",
        show_alert=True,
    )
    await _refresh_company_view(callback, company_id)


@router.callback_query(F.data.startswith("company:fire:"))
async def cb_fire(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    company_id = int(parts[2])
    count_str = parts[3] if len(parts) > 3 else "1"
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not company or not user or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            if company.employee_count <= 1:
                await callback.answer("至少需要保留1名员工", show_alert=True)
                return

            desired = int(count_str)
            max_fireable = company.employee_count - 1
            fire_count = min(desired, max_fireable)
            if fire_count <= 0:
                await callback.answer("至少需要保留1名员工", show_alert=True)
                return
            company.employee_count -= fire_count

    await callback.answer(
        f"裁员完成! 裁了{fire_count}人",
        show_alert=True,
    )
    await _refresh_company_view(callback, company_id)


# ---- 公司升级 ----

@router.callback_query(F.data.startswith("company:upgrade:"))
async def cb_upgrade(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not company or not user or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            ok, msg = await upgrade_company(session, company_id)

    await callback.answer(msg, show_alert=True)
    if ok:
        await _refresh_company_view(callback, company_id)


# ---- 公司改名 ----

@router.callback_query(F.data.startswith("company:rename:"))
async def cb_rename(callback: types.CallbackQuery, state: FSMContext):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return
        user = await get_user_by_tg_id(session, tg_id)
        if not user or company.owner_id != user.id:
            await callback.answer("只有老板才能改名", show_alert=True)
            return

    await state.set_state(RenameCompanyState.waiting_new_name)
    await state.update_data(company_id=company_id)
    await callback.message.edit_text(f"当前名称: {company.name}\n请输入新公司名称 (2-16字):")
    await callback.answer()


@router.message(RenameCompanyState.waiting_new_name)
async def on_new_name(message: types.Message, state: FSMContext):
    new_name = message.text.strip()
    if not (2 <= len(new_name) <= 16):
        await message.answer("公司名称需要2-16个字符:")
        return

    data = await state.get_data()
    company_id = data["company_id"]

    from sqlalchemy import select
    async with async_session() as session:
        async with session.begin():
            from db.models import Company
            exists = await session.execute(select(Company).where(Company.name == new_name))
            if exists.scalar_one_or_none():
                await message.answer("名称已被使用，请换一个:")
                return
            company = await session.get(Company, company_id)
            if not company:
                await message.answer("公司不存在")
                await state.clear()
                return
            old_name = company.name
            company.name = new_name

    await message.answer(f"公司改名成功! {old_name} → {new_name}")
    await state.clear()
    from keyboards.menus import main_menu_kb
    await message.answer("返回主菜单:", reply_markup=main_menu_kb())

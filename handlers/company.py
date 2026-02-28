"""公司相关处理器。/company 在私聊和群组中均可使用。"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from handlers.common import group_only
from keyboards.menus import company_detail_kb, company_list_kb
from services.company_service import (
    create_company,
    get_companies_by_owner,
    get_company_by_id,
    get_company_type_info,
    get_company_valuation,
    load_company_types,
)
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_traffic

router = Router()


class CreateCompanyState(StatesGroup):
    waiting_type = State()
    waiting_name = State()


class RenameCompanyState(StatesGroup):
    waiting_new_name = State()


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
        if message.chat.type == "private":
            await message.answer("你还没有公司。请在群组频道中创建公司。")
        else:
            await message.answer(
                "你还没有公司。",
                reply_markup=company_list_kb([]),
            )
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

    items = [(c.id, c.name) for c in companies]
    await callback.message.edit_text("🏢 你的公司列表:", reply_markup=company_list_kb(items))
    await callback.answer()


@router.callback_query(F.data.startswith("company:view:"))
async def cb_company_view(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return
        user = await get_user_by_tg_id(session, tg_id)
        valuation = await get_company_valuation(session, company)
        is_owner = user and company.owner_id == user.id

        # 统计股东数
        from db.models import Shareholder
        from sqlalchemy import select, func as sqlfunc
        sh_count_result = await session.execute(
            select(sqlfunc.count()).where(Shareholder.company_id == company_id)
        )
        sh_count = sh_count_result.scalar()

        # 统计产品数
        from db.models import Product
        prod_count_result = await session.execute(
            select(sqlfunc.count()).where(Product.company_id == company_id)
        )
        prod_count = prod_count_result.scalar()

    # 公司类型信息
    type_info = get_company_type_info(company.company_type)
    type_display = f"{type_info['emoji']} {type_info['name']}" if type_info else company.company_type

    # 税务/薪资计算
    from config import settings as cfg
    daily_tax = int(company.daily_revenue * cfg.tax_rate)
    salary_cost = company.employee_count * cfg.employee_salary_base
    max_employees = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1)
    if type_info and type_info.get("extra_employee_limit"):
        max_employees += type_info["extra_employee_limit"]

    text = (
        f"🏢 {company.name} (ID: {company.id})\n"
        f"类型: {type_display}\n"
        "─" * 24 + "\n"
        f"💰 总资金: {fmt_traffic(company.total_funds)}\n"
        f"📈 日营收: {fmt_traffic(company.daily_revenue)}\n"
        f"🏷 估值: {fmt_traffic(valuation)}\n"
        f"📊 等级: Lv.{company.level}\n"
        f"👥 股东数: {sh_count}\n"
        f"👷 员工: {company.employee_count}/{max_employees}\n"
        f"📦 产品数: {prod_count}\n"
        f"🏛 日纳税: {fmt_traffic(daily_tax)}\n"
        f"💼 日薪资: {fmt_traffic(salary_cost)}\n"
    )
    await callback.message.edit_text(
        text,
        reply_markup=company_detail_kb(company_id, is_owner),
    )
    await callback.answer()


# ---- 创建公司（仅群组）：先选类型再输入名称 ----

@router.callback_query(F.data == "company:create", group_only)
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


@router.callback_query(CreateCompanyState.waiting_type, F.data.startswith("company:type:"), group_only)
async def cb_company_type_selected(callback: types.CallbackQuery, state: FSMContext):
    company_type = callback.data.split(":")[2]
    await state.update_data(company_type=company_type)
    await state.set_state(CreateCompanyState.waiting_name)
    type_info = get_company_type_info(company_type)
    name = type_info["name"] if type_info else company_type
    await callback.message.edit_text(f"已选择: {name}\n\n请输入新公司名称 (2-16字):")
    await callback.answer()


@router.message(CreateCompanyState.waiting_name, group_only)
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
        from keyboards.menus import main_menu_kb
        await message.answer("返回主菜单:", reply_markup=main_menu_kb())


# ---- 招聘/裁员（仅群组）----

@router.callback_query(F.data.startswith("company:hire:"), group_only)
async def cb_hire(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id
    from config import settings as cfg

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not company or not user or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            type_info = get_company_type_info(company.company_type)
            max_emp = cfg.base_employee_limit + cfg.employee_limit_per_level * (company.level - 1)
            if type_info and type_info.get("extra_employee_limit"):
                max_emp += type_info["extra_employee_limit"]
            if company.employee_count >= max_emp:
                await callback.answer(f"已达员工上限 ({max_emp}人)，升级公司可提升上限", show_alert=True)
                return
            hire_cost = cfg.employee_salary_base * 10
            from services.company_service import add_funds
            ok = await add_funds(session, company_id, -hire_cost)
            if not ok:
                await callback.answer(f"公司资金不足，招聘需要{hire_cost}流量", show_alert=True)
                return
            company.employee_count += 1

    await callback.answer(f"招聘成功! 当前员工: {company.employee_count}人", show_alert=True)


@router.callback_query(F.data.startswith("company:fire:"), group_only)
async def cb_fire(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])
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
            company.employee_count -= 1

    await callback.answer(f"裁员完成! 当前员工: {company.employee_count}人", show_alert=True)


# ---- 公司改名（仅群组，通过公司ID关联不受影响）----

@router.callback_query(F.data.startswith("company:rename:"), group_only)
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


@router.message(RenameCompanyState.waiting_new_name, group_only)
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
            # 检查重名
            exists = await session.execute(select(Company).where(Company.name == new_name))
            if exists.scalar_one_or_none():
                await message.answer("名称已被使用，请换一个:")
                return
            company = await session.get(Company, company_id)
            if company:
                old_name = company.name
                company.name = new_name

    await message.answer(f"公司改名成功! {old_name} → {new_name}")
    await state.clear()
    from keyboards.menus import main_menu_kb
    await message.answer("返回主菜单:", reply_markup=main_menu_kb())

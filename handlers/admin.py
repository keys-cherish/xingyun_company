"""Buff一览和管理员配置处理器。"""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from handlers.common import group_only
from keyboards.menus import main_menu_kb
from services.ad_service import get_active_ad_info
from services.company_service import get_company_by_id, get_company_type_info
from services.cooperation_service import get_active_cooperations
from services.user_service import get_user_by_tg_id
from utils.formatters import fmt_reputation_buff, reputation_buff_multiplier

router = Router()


# ---- Buff一览 ----

@router.callback_query(F.data.startswith("buff:list:"))
async def cb_buff_list(callback: types.CallbackQuery):
    company_id = int(callback.data.split(":")[2])

    async with async_session() as session:
        company = await get_company_by_id(session, company_id)
        if not company:
            await callback.answer("公司不存在", show_alert=True)
            return

        from db.models import User
        owner = await session.get(User, company.owner_id)
        rep = owner.reputation if owner else 0

        # 合作Buff
        coops = await get_active_cooperations(session, company_id)
        coop_buff = max((c.bonus_multiplier for c in coops), default=0.0)

    # 声望Buff（不可叠加，取最高）
    rep_mult = reputation_buff_multiplier(rep)
    rep_buff_pct = (rep_mult - 1.0) * 100

    # 广告Buff
    ad_info = await get_active_ad_info(company_id)
    ad_buff_pct = ad_info["boost_pct"] * 100 if ad_info else 0
    ad_days = ad_info["remaining_days"] if ad_info else 0

    # 公司类型Buff
    type_info = get_company_type_info(company.company_type)
    type_income_buff = type_info.get("income_bonus", 0) * 100 if type_info else 0
    type_research_buff = type_info.get("research_speed_bonus", 0) * 100 if type_info else 0
    type_cost_buff = type_info.get("cost_bonus", 0) * 100 if type_info else 0

    lines = [
        f"📋 {company.name} — Buff一览",
        "─" * 24,
        "",
        "【声望Buff】(不可叠加，取最高)",
        f"  当前声望: {rep}",
        f"  营收加成: +{rep_buff_pct:.1f}%",
        "",
        "【合作Buff】(不可叠加，取最高)",
        f"  当前合作数: {len(coops)}",
        f"  营收加成: +{coop_buff*100:.0f}%",
        "",
        "【广告Buff】(有时效)",
    ]
    if ad_info:
        lines.append(f"  活动广告: {ad_info.get('name', '广告')}")
        lines.append(f"  营收加成: +{ad_buff_pct:.0f}%")
        lines.append(f"  剩余天数: {ad_days}天")
    else:
        lines.append("  无活动广告")

    lines += [
        "",
        "【路演Buff】(通过路演随机获得)",
        "  声望提升 → 影响声望Buff",
        "  直接流量/积分奖励",
        "",
        f"【公司类型Buff】({type_info['name'] if type_info else '未知'})",
        f"  收入加成: {'+' if type_income_buff >= 0 else ''}{type_income_buff:.0f}%",
        f"  研发速度: {'+' if type_research_buff >= 0 else ''}{type_research_buff:.0f}%",
        f"  成本影响: {'+' if type_cost_buff >= 0 else ''}{type_cost_buff:.0f}%",
        "",
        "【地产Buff】(永久)",
        "  地产提供稳定日收入",
        "  地产收入不受其他Buff影响",
        "",
        "【AI研发Buff】(永久)",
        "  通过AI研发永久提升产品收入",
        "  提升幅度取决于方案评分(1-100%)",
        "─" * 24,
        "注: 声望/合作/广告Buff均不可叠加，取各类别最高值",
    ]

    from keyboards.menus import company_detail_kb
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=company_detail_kb(company_id, True),
    )
    await callback.answer()


# ---- 管理员配置菜单 ----
# 管理员通过 /admin 命令访问，可修改游戏参数

ADMIN_TG_IDS: set[int] = set()  # 在bot启动时从环境变量加载


class AdminConfigState(StatesGroup):
    waiting_param_value = State()


def _admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="初始流量", callback_data="admin:cfg:initial_traffic")],
        [InlineKeyboardButton(text="创建公司费用", callback_data="admin:cfg:company_creation_cost")],
        [InlineKeyboardButton(text="最低老板持股%", callback_data="admin:cfg:min_owner_share_pct")],
        [InlineKeyboardButton(text="税率", callback_data="admin:cfg:tax_rate")],
        [InlineKeyboardButton(text="分红比例", callback_data="admin:cfg:dividend_pct")],
        [InlineKeyboardButton(text="员工基础薪资", callback_data="admin:cfg:employee_salary_base")],
        [InlineKeyboardButton(text="路演费用", callback_data="admin:cfg:roadshow_cost")],
        [InlineKeyboardButton(text="路演冷却(秒)", callback_data="admin:cfg:roadshow_cooldown_seconds")],
        [InlineKeyboardButton(text="产品创建费用", callback_data="admin:cfg:product_create_cost")],
        [InlineKeyboardButton(text="手动结算", callback_data="admin:settle")],
        [InlineKeyboardButton(text="🔙 关闭", callback_data="admin:close")],
    ])


@router.message(Command("admin"), group_only)
async def cmd_admin(message: types.Message):
    tg_id = message.from_user.id
    from config import settings
    # 管理员检查：如果设置了admin列表则检查，否则允许所有人（开发模式）
    admin_ids = settings.allowed_chat_id_set  # 复用或单独配置
    # 简单方案：首个注册的用户就是管理员，或者通过环境变量配置
    # 这里暂时允许所有人访问管理面板，生产环境应配置ADMIN_TG_IDS

    await message.answer(
        "⚙️ 管理员配置面板\n当前参数可实时修改:",
        reply_markup=_admin_menu_kb(),
    )


@router.callback_query(F.data.startswith("admin:cfg:"), group_only)
async def cb_admin_cfg(callback: types.CallbackQuery, state: FSMContext):
    param = callback.data.split(":")[2]
    from config import settings
    current = getattr(settings, param, "未知")
    await callback.message.edit_text(
        f"⚙️ 修改参数: {param}\n当前值: {current}\n\n请输入新值:"
    )
    await state.set_state(AdminConfigState.waiting_param_value)
    await state.update_data(param=param)
    await callback.answer()


@router.message(AdminConfigState.waiting_param_value, group_only)
async def on_admin_param_value(message: types.Message, state: FSMContext):
    data = await state.get_data()
    param = data["param"]
    value_str = message.text.strip()

    from config import settings
    current = getattr(settings, param, None)
    if current is None:
        await message.answer("参数不存在")
        await state.clear()
        return

    try:
        if isinstance(current, int):
            new_value = int(value_str)
        elif isinstance(current, float):
            new_value = float(value_str)
        else:
            new_value = value_str
        setattr(settings, param, new_value)
        await message.answer(
            f"✅ 参数 {param} 已更新为: {new_value}",
            reply_markup=_admin_menu_kb(),
        )
    except (ValueError, TypeError):
        await message.answer(f"无效的值，需要 {type(current).__name__} 类型，请重新输入:")
        return

    await state.clear()


@router.callback_query(F.data == "admin:settle", group_only)
async def cb_admin_settle(callback: types.CallbackQuery):
    """手动触发结算。"""
    await callback.answer("正在执行结算...", show_alert=True)
    from services.settlement_service import settle_all, format_daily_report
    async with async_session() as session:
        async with session.begin():
            reports = await settle_all(session)

    lines = [f"手动结算完成，处理了 {len(reports)} 家公司:"]
    for company, report, events in reports:
        lines.append(format_daily_report(company, report, events))
        lines.append("")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(截断)"
    await callback.message.edit_text(text, reply_markup=_admin_menu_kb())


@router.callback_query(F.data == "admin:close")
async def cb_admin_close(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()

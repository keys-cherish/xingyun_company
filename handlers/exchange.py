"""Exchange, shop, and black market handler — unified 商业交易所."""

from __future__ import annotations

from aiogram import F, Router, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.engine import async_session
from services.company_service import get_company_by_id
from services.shop_service import (
    buy_black_market_item,
    buy_item,
    get_active_buffs,
    get_black_market_items,
    load_shop_items,
)
from services.user_service import (
    exchange_credits_for_quota,
    exchange_points_for_traffic,
    exchange_quota_for_credits,
    get_credit_to_quota_rate,
    get_points,
    get_quota_mb,
    get_user_by_tg_id,
    BASE_CREDIT_TO_QUOTA_RATE,
)
from utils.formatters import fmt_traffic, fmt_quota
from keyboards.menus import tag_kb

router = Router()


# ---- Exchange menu ----

def _exchange_menu_kb(tg_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💱 积分→储备", callback_data="exchange:c2q"),
            InlineKeyboardButton(text="💱 储备→积分", callback_data="exchange:q2c"),
        ],
        [
            InlineKeyboardButton(text="🎁 荣誉点→积分", callback_data="exchange:p2c"),
        ],
        [
            InlineKeyboardButton(text="🛒 道具商城", callback_data="shop:list"),
        ],
        [
            InlineKeyboardButton(text="🌙 黑市特惠", callback_data="blackmarket:list"),
        ],
        [
            InlineKeyboardButton(text="🔙 返回", callback_data="menu:company"),
        ],
    ])
    return tag_kb(kb, tg_id)


@router.callback_query(F.data == "menu:exchange")
async def cb_exchange_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    rate = get_credit_to_quota_rate(tg_id)
    diff = rate - BASE_CREDIT_TO_QUOTA_RATE
    pct = diff / BASE_CREDIT_TO_QUOTA_RATE * 100
    arrow = "↑" if diff > 0 else "↓" if diff < 0 else "─"
    sign = "+" if pct >= 0 else ""

    text = (
        f"🏦 商业交易所\n"
        f"{'─' * 24}\n"
        f"💱 当前汇率: 1储备积分 = {rate} 积分 ({arrow}{sign}{pct:.0f}%)\n"
    )
    await callback.message.edit_text(text, reply_markup=_exchange_menu_kb(tg_id=callback.from_user.id))
    await callback.answer()


# ---- Credit -> Quota ----

def _c2q_amounts_kb(rate: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    amounts = [1_000, 3_000, 8_000, 15_000]
    buttons = [
        [InlineKeyboardButton(
            text=f"花费 {a:,} 积分 (~{max(1, a // rate)}储备积分)",
            callback_data=f"exchange:c2q:{a}",
        )]
        for a in amounts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:exchange")])
    return tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)


@router.callback_query(F.data == "exchange:c2q")
async def cb_c2q_menu(callback: types.CallbackQuery):
    rate = get_credit_to_quota_rate(callback.from_user.id)
    await callback.message.edit_text(
        f"💱 积分 → 储备积分\n当前汇率: {rate} 积分 = 1 储备积分\n\n选择兑换金额:",
        reply_markup=_c2q_amounts_kb(rate, tg_id=callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exchange:c2q:"))
async def cb_c2q_do(callback: types.CallbackQuery):
    amount = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            ok, msg = await exchange_credits_for_quota(session, tg_id, amount)

    await callback.answer(msg, show_alert=True)
    if ok:
        # Refresh exchange menu
        rate = get_credit_to_quota_rate(tg_id)
        diff = rate - BASE_CREDIT_TO_QUOTA_RATE
        pct = diff / BASE_CREDIT_TO_QUOTA_RATE * 100
        arrow = "↑" if diff > 0 else "↓" if diff < 0 else "─"
        sign = "+" if pct >= 0 else ""
        text = (
            f"🏦 商业交易所\n"
            f"{'─' * 24}\n"
            f"💱 当前汇率: 1储备积分 = {rate} 积分 ({arrow}{sign}{pct:.0f}%)\n"
        )
        try:
            await callback.message.edit_text(text, reply_markup=_exchange_menu_kb(tg_id=tg_id))
        except Exception:
            pass


# ---- Quota -> Credit (reverse, 20% penalty) ----

def _q2c_amounts_kb(tg_id: int | None = None) -> InlineKeyboardMarkup:
    amounts = [10, 50, 100, 500]
    buttons = [
        [InlineKeyboardButton(
            text=f"兑出 {a} 储备积分",
            callback_data=f"exchange:q2c:{a}",
        )]
        for a in amounts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:exchange")])
    return tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)


@router.callback_query(F.data == "exchange:q2c")
async def cb_q2c_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    quota = await get_quota_mb(tg_id)
    rate = get_credit_to_quota_rate(tg_id)
    reverse_rate = int(rate * 0.8)  # 20% penalty

    await callback.message.edit_text(
        f"💱 储备积分 → 积分 (有损兑换)\n"
        f"反向汇率: 1储备积分 = {reverse_rate} 积分 (正向的80%)\n"
        f"当前储备: {fmt_quota(quota)}\n\n"
        f"选择兑出数量:",
        reply_markup=_q2c_amounts_kb(tg_id=tg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exchange:q2c:"))
async def cb_q2c_do(callback: types.CallbackQuery):
    amount_mb = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            ok, msg = await exchange_quota_for_credits(session, tg_id, amount_mb)

    await callback.answer(msg, show_alert=True)


# ---- Points -> Credit ----

def _p2c_amounts_kb(tg_id: int | None = None) -> InlineKeyboardMarkup:
    amounts = [100, 500, 1000, 5000]
    buttons = [
        [InlineKeyboardButton(
            text=f"兑换 {a} 荣誉点 → {a // 10} 积分",
            callback_data=f"exchange:p2c:{a}",
        )]
        for a in amounts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:exchange")])
    return tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), tg_id)


@router.callback_query(F.data == "exchange:p2c")
async def cb_p2c_menu(callback: types.CallbackQuery):
    tg_id = callback.from_user.id
    points = await get_points(tg_id)
    await callback.message.edit_text(
        f"🎁 荣誉点 → 积分\n"
        f"汇率: 10 荣誉点 = 1 积分\n"
        f"当前荣誉点: {points:,}\n\n"
        f"选择兑换数量:",
        reply_markup=_p2c_amounts_kb(tg_id=tg_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exchange:p2c:"))
async def cb_p2c_do(callback: types.CallbackQuery):
    points_amount = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            ok, msg = await exchange_points_for_traffic(session, tg_id, points_amount)

    await callback.answer(msg, show_alert=True)


# ---- Shop ----

@router.callback_query(F.data == "shop:list")
async def cb_shop_list(callback: types.CallbackQuery):
    items = load_shop_items()

    lines = [
        "🛒 道具商城",
        "─" * 24,
    ]
    buttons = []
    for key, item in items.items():
        lines.append(f"{item['name']} — {item['price']:,} 积分")
        lines.append(f"  {item['description']}")
        lines.append("")
        buttons.append([InlineKeyboardButton(
            text=f"{item['name']} ({item['price']:,}💰)",
            callback_data=f"shop:select:{key}",
        )])

    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:exchange")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop:select:"))
async def cb_shop_select(callback: types.CallbackQuery):
    """Show item detail and ask which company to apply the buff to."""
    item_key = callback.data.split(":")[2]
    items = load_shop_items()
    if item_key not in items:
        await callback.answer("无效道具", show_alert=True)
        return

    item = items[item_key]
    tg_id = callback.from_user.id

    # Get user's companies
    from services.company_service import get_companies_by_owner
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你没有公司，无法使用道具", show_alert=True)
        return

    if len(companies) == 1:
        # Directly buy for the only company
        async with async_session() as session:
            async with session.begin():
                ok, msg = await buy_item(session, tg_id, companies[0].id, item_key)
        await callback.answer(msg, show_alert=True)
        return

    # Multiple companies — let user choose
    buttons = [
        [InlineKeyboardButton(
            text=c.name,
            callback_data=f"shop:buy:{item_key}:{c.id}",
        )]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="shop:list")])

    await callback.message.edit_text(
        f"为哪家公司购买 {item['name']}?",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("shop:buy:"))
async def cb_shop_buy(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    item_key = parts[2]
    company_id = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not user:
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            if not company or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            ok, msg = await buy_item(session, tg_id, company_id, item_key)

    await callback.answer(msg, show_alert=True)


# ---- Black Market ----

@router.callback_query(F.data == "blackmarket:list")
async def cb_blackmarket_list(callback: types.CallbackQuery):
    deals = await get_black_market_items()

    lines = [
        "🌙 黑市特惠 — 每日刷新，先到先得",
        "─" * 24,
    ]
    buttons = []
    for i, deal in enumerate(deals):
        stock_text = f"库存: {deal['stock']}" if deal['stock'] > 0 else "已售罄"
        lines.append(
            f"{deal['name']} — {deal['price']:,} 积分 "
            f"(原价 {deal['original_price']:,}, 省{deal['discount_pct']}%)"
        )
        lines.append(f"  {deal['description']} [{stock_text}]")
        lines.append("")
        if deal['stock'] > 0:
            buttons.append([InlineKeyboardButton(
                text=f"购买 {deal['name']} ({deal['price']:,}💰)",
                callback_data=f"blackmarket:select:{i}",
            )])

    if not deals:
        lines.append("今日暂无特惠")

    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:exchange")])

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("blackmarket:select:"))
async def cb_blackmarket_select(callback: types.CallbackQuery):
    """Buy black market item — select company if multiple."""
    index = int(callback.data.split(":")[2])
    tg_id = callback.from_user.id

    from services.company_service import get_companies_by_owner
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            await callback.answer("请先 /company_create 创建公司", show_alert=True)
            return
        companies = await get_companies_by_owner(session, user.id)

    if not companies:
        await callback.answer("你没有公司，无法使用道具", show_alert=True)
        return

    if len(companies) == 1:
        async with async_session() as session:
            async with session.begin():
                ok, msg = await buy_black_market_item(session, tg_id, companies[0].id, index)
        await callback.answer(msg, show_alert=True)
        return

    buttons = [
        [InlineKeyboardButton(
            text=c.name,
            callback_data=f"blackmarket:buy:{index}:{c.id}",
        )]
        for c in companies
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="blackmarket:list")])

    await callback.message.edit_text(
        "为哪家公司购买黑市道具?",
        reply_markup=tag_kb(InlineKeyboardMarkup(inline_keyboard=buttons), callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("blackmarket:buy:"))
async def cb_blackmarket_buy(callback: types.CallbackQuery):
    parts = callback.data.split(":")
    index = int(parts[2])
    company_id = int(parts[3])
    tg_id = callback.from_user.id

    async with async_session() as session:
        async with session.begin():
            user = await get_user_by_tg_id(session, tg_id)
            company = await get_company_by_id(session, company_id)
            if not user:
                await callback.answer("请先 /company_create 创建公司", show_alert=True)
                return
            if not company or company.owner_id != user.id:
                await callback.answer("无权操作", show_alert=True)
                return
            ok, msg = await buy_black_market_item(session, tg_id, company_id, index)

    await callback.answer(msg, show_alert=True)

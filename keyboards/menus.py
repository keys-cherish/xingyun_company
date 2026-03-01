"""Inline keyboard layouts for menus."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def tag_kb(kb: InlineKeyboardMarkup, tg_id: int | None) -> InlineKeyboardMarkup:
    """Tag callbacks with panel owner tg_id for middleware auth."""
    if tg_id is None:
        return kb
    new_rows = []
    for row in kb.inline_keyboard:
        new_row = []
        for btn in row:
            if btn.callback_data:
                new_row.append(InlineKeyboardButton(
                    text=btn.text,
                    callback_data=f"{btn.callback_data}|{tg_id}",
                ))
            else:
                new_row.append(btn)
        new_rows.append(new_row)
    return InlineKeyboardMarkup(inline_keyboard=new_rows)


# ---- Main menu (simplified: redirects to company view) ----

def main_menu_kb(tg_id: int | None = None) -> InlineKeyboardMarkup:
    """Simplified menu — goes to company view (the main hub now)."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🏢 我的公司", callback_data="menu:company"),
            InlineKeyboardButton(text="📊 个人面板", callback_data="menu:profile"),
        ],
        [
            InlineKeyboardButton(text="📈 排行榜", callback_data="menu:leaderboard"),
            InlineKeyboardButton(text="🎯 周任务", callback_data="menu:quest"),
        ],
    ])
    return tag_kb(kb, tg_id)


# ---- Company ----

def company_list_kb(companies: list[tuple[int, str]], tg_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"company:view:{cid}")]
        for cid, name in companies
    ]
    buttons.append([InlineKeyboardButton(text="➕ 创建公司", callback_data="company:create")])
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


def company_detail_kb(company_id: int, is_owner: bool, tg_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="👥 股东", callback_data=f"shareholder:list:{company_id}"),
            InlineKeyboardButton(text="📦 产品", callback_data=f"product:list:{company_id}"),
        ],
        [
            InlineKeyboardButton(text="🔬 科研", callback_data=f"research:list:{company_id}"),
            InlineKeyboardButton(text="🏗 地产", callback_data=f"realestate:list:{company_id}"),
        ],
    ]
    if is_owner:
        buttons.append([
            InlineKeyboardButton(text="⬆️ 升级公司", callback_data=f"company:upgrade:{company_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🎤 路演", callback_data=f"roadshow:do:{company_id}"),
            InlineKeyboardButton(text="🤝 合作状态", callback_data=f"cooperation:init:{company_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="⚙️ 经营策略", callback_data=f"ops:menu:{company_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="Hire +1", callback_data=f"company:hire:{company_id}:1"),
            InlineKeyboardButton(text="Hire +5", callback_data=f"company:hire:{company_id}:5"),
            InlineKeyboardButton(text="Hire Max", callback_data=f"company:hire:{company_id}:max"),
        ])
        buttons.append([
            InlineKeyboardButton(text="Fire -1", callback_data=f"company:fire:{company_id}:1"),
            InlineKeyboardButton(text="Fire -5", callback_data=f"company:fire:{company_id}:5"),
        ])
        buttons.append([
            InlineKeyboardButton(text="📢 广告", callback_data=f"ad:menu:{company_id}"),
            InlineKeyboardButton(text="🧪 AI研发", callback_data=f"aird:start:{company_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="✏️ 改名", callback_data=f"company:rename:{company_id}"),
            InlineKeyboardButton(text="📋 Buff一览", callback_data=f"buff:list:{company_id}"),
        ])
        buttons.append([
            InlineKeyboardButton(text="📊 个人面板", callback_data="menu:profile"),
            InlineKeyboardButton(text="📈 排行榜", callback_data="menu:leaderboard"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🏦 交易所", callback_data="menu:exchange"),
            InlineKeyboardButton(text="🎯 周任务", callback_data="menu:quest"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="💵 注资", callback_data=f"shareholder:invest:{company_id}"),
        ])
    buttons.append([
        InlineKeyboardButton(text="📋 公司列表", callback_data="menu:company_list"),
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


# ---- Shareholders ----

def invest_kb(company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    amounts = [500, 1000, 2000, 5000]
    buttons = [
        [InlineKeyboardButton(text=f"注资 {a:,} 金币", callback_data=f"shareholder:doinvest:{company_id}:{a}")]
        for a in amounts
    ]
    buttons.append([InlineKeyboardButton(text="✍️ 自定义金额（文本）", callback_data=f"shareholder:input:{company_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


def shareholder_list_kb(company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 去注资", callback_data=f"shareholder:invest:{company_id}")],
        [InlineKeyboardButton(text="🔙 返回公司", callback_data=f"company:view:{company_id}")],
    ])
    return tag_kb(kb, tg_id)


# ---- Research ----

def tech_list_kb(techs: list[dict], company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    from utils.formatters import fmt_duration
    buttons = [
        [InlineKeyboardButton(
            text=f"{t['name']} ({t['cost']:,}💰 {fmt_duration(t.get('effective_duration_seconds', t.get('duration_seconds', 3600)))})",
            callback_data=f"research:start:{company_id}:{t['tech_id']}",
        )]
        for t in techs
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


# ---- Products ----

def product_template_kb(templates: list[dict], company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"{t['name']} (💰{t['base_daily_income']:,}/日)",
            callback_data=f"product:create:{company_id}:{t['product_key']}",
        )]
        for t in templates
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


def product_detail_kb(product_id: int, company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬆️ 升级x1", callback_data=f"product:upgrade:{product_id}:1"),
            InlineKeyboardButton(text="⬆️⬆️ 升级x5", callback_data=f"product:upgrade:{product_id}:5"),
        ],
        [InlineKeyboardButton(text="🔙 返回", callback_data=f"product:list:{company_id}")],
    ])
    return tag_kb(kb, tg_id)


# ---- Real Estate ----

def building_list_kb(buildings: list[dict], company_id: int, tg_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=f"{b['name']} (💰{b['purchase_price']:,} → {b['daily_dividend']:,}/日)",
            callback_data=f"realestate:buy:{company_id}:{b['key']}",
        )]
        for b in buildings
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data=f"company:view:{company_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


# ---- Exchange ----

def exchange_kb(rate_per_mb: int | None = None, tg_id: int | None = None) -> InlineKeyboardMarkup:
    spend_amounts = [1_000, 3_000, 8_000, 15_000]
    safe_rate = max(1, rate_per_mb or 120)
    buttons = [
        [InlineKeyboardButton(
            text=f"花费 {amount:,} 金币 (~{max(1, amount // safe_rate)}MB)",
            callback_data=f"exchange:{amount}",
        )]
        for amount in spend_amounts
    ]
    buttons.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    return tag_kb(kb, tg_id)


# ---- Pagination helper ----

def paginated_kb(
    items: list[InlineKeyboardButton],
    page: int,
    total_pages: int,
    prefix: str,
    tg_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows = [[btn] for btn in items]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"{prefix}:page:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="➡️ 下一页", callback_data=f"{prefix}:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 返回", callback_data="menu:main")])
    kb = InlineKeyboardMarkup(inline_keyboard=rows)
    return tag_kb(kb, tg_id)


# ---- Confirm ----

def confirm_kb(action: str, tg_id: int | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ 确认", callback_data=f"confirm:{action}"),
            InlineKeyboardButton(text="❌ 取消", callback_data="cancel"),
        ],
    ])
    return tag_kb(kb, tg_id)

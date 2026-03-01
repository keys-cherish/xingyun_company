"""@机器人 即时AI对话处理器。"""

from __future__ import annotations

import re

from aiogram import F, Router, types

from cache.redis_client import get_redis
from config import settings
from services.ai_chat_service import ask_ai_chat
from db.engine import async_session
from db.models import CompanyOperationProfile
from services.company_service import get_companies_by_owner, get_company_type_info, get_level_info
from services.user_service import get_user_by_tg_id, get_points

router = Router()

AI_MENTION_LIMIT_PER_MINUTE = 10
AI_MENTION_WINDOW_SECONDS = 60


def _is_admin_or_super_admin(tg_id: int) -> bool:
    return tg_id in settings.super_admin_tg_id_set or tg_id in settings.admin_tg_id_set


def _extract_prompt_without_mention(text: str, bot_username: str) -> str:
    username = re.escape(bot_username)
    mention_pattern = rf"(?<![A-Za-z0-9_])@{username}(?![A-Za-z0-9_])"
    cleaned = re.sub(mention_pattern, "", text, flags=re.IGNORECASE)
    return cleaned.strip()


async def _build_user_company_context(tg_id: int) -> str:
    async with async_session() as session:
        user = await get_user_by_tg_id(session, tg_id)
        if not user:
            return "用户未注册公司系统。"

        companies = await get_companies_by_owner(session, user.id)
        if not companies:
            points = await get_points(tg_id)
            return (
                f"用户: {user.tg_name}\n"
                f"声望: {user.reputation}\n"
                f"荣誉点: {points:,}\n"
                "公司: 暂无"
            )

        company = companies[0]
        type_info = get_company_type_info(company.company_type) or {}
        level_info = get_level_info(company.level) or {}
        op = await session.get(CompanyOperationProfile, company.id)
        points = await get_points(tg_id)

        type_name = type_info.get("name", company.company_type)
        level_name = level_info.get("name", "未知等级")
        ethics = op.ethics if op else 60
        culture = op.culture if op else 50
        regulation = op.regulation_pressure if op else 40

        return (
            f"用户: {user.tg_name}\n"
            f"声望: {user.reputation}\n"
            f"荣誉点: {points:,}\n"
            f"公司: {company.name}\n"
            f"行业: {type_name}\n"
            f"等级: Lv.{company.level} {level_name}\n"
            f"资金: {company.total_funds:,} 积分\n"
            f"日营收: {company.daily_revenue:,} 积分\n"
            f"员工: {company.employee_count}\n"
            f"道德: {ethics}/100\n"
            f"文化: {culture}/100\n"
            f"监管压力: {regulation}/100"
        )


@router.message(F.text & ~F.text.startswith("/"))
async def on_ai_bot_mention(message: types.Message):
    if not message.from_user or message.from_user.is_bot:
        return

    text = (message.text or "").strip()
    if not text:
        return

    bot_user = await message.bot.get_me()
    bot_username = (bot_user.username or "").strip()

    mention_hit = False
    if bot_username:
        username = re.escape(bot_username)
        mention_pattern = rf"(?<![A-Za-z0-9_])@{username}(?![A-Za-z0-9_])"
        mention_hit = bool(re.search(mention_pattern, text, flags=re.IGNORECASE))

    reply_to = message.reply_to_message
    reply_to_bot = bool(
        reply_to
        and reply_to.from_user
        and bot_user.id == reply_to.from_user.id
    )

    if not mention_hit and not reply_to_bot:
        return

    tg_id = message.from_user.id
    if not _is_admin_or_super_admin(tg_id):
        r = await get_redis()
        key = f"ai:mention:minute:{tg_id}"
        current = await r.incr(key)
        if current == 1:
            await r.expire(key, AI_MENTION_WINDOW_SECONDS)
        if current > AI_MENTION_LIMIT_PER_MINUTE:
            await message.reply("⏳ 你调用太频繁了：每人每分钟最多 10 次。")
            return

    prompt = _extract_prompt_without_mention(text, bot_username) if bot_username else text
    if not prompt:
        await message.reply("请在 @我 后面加上问题内容。")
        return

    company_context = await _build_user_company_context(tg_id)
    prompt_with_context = (
        "请基于以下提问者经营数据给出建议。\n\n"
        f"【提问者信息】\n{company_context}\n\n"
        f"【用户问题】\n{prompt}\n\n"
        "要求：优先给可执行建议，必要时给简短分步方案。"
    )

    pending = await message.reply("🤖 努力思考中，请稍等…")
    reply = await ask_ai_chat(prompt_with_context)
    try:
        await pending.edit_text(reply)
    except Exception:
        await message.reply(reply)

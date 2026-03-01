"""@机器人 即时AI对话处理器。"""

from __future__ import annotations

import re

from aiogram import F, Router, types

from cache.redis_client import get_redis
from config import settings
from services.ai_chat_service import ask_ai_chat

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


@router.message(F.text)
async def on_ai_bot_mention(message: types.Message):
    if not message.from_user or message.from_user.is_bot:
        return

    text = (message.text or "").strip()
    if not text:
        return

    # 命令（包括 /cmd@bot）不走 AI 闲聊
    if text.startswith("/"):
        return

    bot_user = await message.bot.get_me()
    bot_username = (bot_user.username or "").strip()
    if not bot_username:
        return

    username = re.escape(bot_username)
    mention_pattern = rf"(?<![A-Za-z0-9_])@{username}(?![A-Za-z0-9_])"
    if not re.search(mention_pattern, text, flags=re.IGNORECASE):
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

    prompt = _extract_prompt_without_mention(text, bot_username)
    if not prompt:
        await message.reply("请在 @我 后面加上问题内容。")
        return

    reply = await ask_ai_chat(prompt)
    await message.reply(reply)

"""Global chat/topic restriction middleware."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config import settings


def _is_allowed_group_topic(chat_id: int, thread_id: int | None) -> bool:
    allowed_chat_ids = settings.allowed_chat_id_set
    allowed_thread_id = settings.allowed_topic_thread_id

    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return False
    if allowed_thread_id > 0 and thread_id != allowed_thread_id:
        return False
    return True


def _restriction_enabled() -> bool:
    return bool(settings.allowed_chat_id_set) or settings.allowed_topic_thread_id > 0


class TopicGateMiddleware(BaseMiddleware):
    """Allow bot interactions only in configured group/topic when enabled."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not _restriction_enabled():
            return await handler(event, data)

        if isinstance(event, Message):
            chat = event.chat
            thread_id = event.message_thread_id
            if chat.type not in ("group", "supergroup") or not _is_allowed_group_topic(chat.id, thread_id):
                await event.answer("❌ 仅允许在指定话题频道使用本机器人。")
                return None
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.message:
            chat = event.message.chat
            thread_id = event.message.message_thread_id
            if chat.type not in ("group", "supergroup") or not _is_allowed_group_topic(chat.id, thread_id):
                await event.answer("❌ 仅允许在指定话题频道使用本机器人。", show_alert=True)
                return None
            return await handler(event, data)

        return await handler(event, data)

"""Redis Stream event middleware."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from cache.redis_client import add_stream_event


class StreamEventMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            text = (event.text or "").strip()
            if text.startswith("/"):
                await add_stream_event(
                    "command",
                    {
                        "chat_id": event.chat.id,
                        "chat_type": event.chat.type,
                        "user_id": event.from_user.id if event.from_user else 0,
                        "text": text[:256],
                        "message_id": event.message_id,
                    },
                )
        elif isinstance(event, CallbackQuery):
            await add_stream_event(
                "callback",
                {
                    "chat_id": event.message.chat.id if event.message else 0,
                    "chat_type": event.message.chat.type if event.message else "",
                    "user_id": event.from_user.id if event.from_user else 0,
                    "data": (event.data or "")[:256],
                    "message_id": event.message.message_id if event.message else 0,
                },
            )
        return await handler(event, data)

"""Panel ownership tracking — prevent users from clicking other users' panels in group chats."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, TelegramObject

from cache.redis_client import get_redis

PANEL_TTL = 86400  # 24 hours


async def mark_panel(chat_id: int, message_id: int, tg_id: int):
    """Record which user owns a panel message."""
    r = await get_redis()
    await r.set(f"panel:{chat_id}:{message_id}", str(tg_id), ex=PANEL_TTL)


async def check_panel_owner(chat_id: int, message_id: int, tg_id: int) -> bool:
    """Return True if tg_id is allowed to interact with this panel."""
    r = await get_redis()
    owner = await r.get(f"panel:{chat_id}:{message_id}")
    if owner is None:
        return True  # No ownership recorded — allow (legacy panels, system panels)
    return int(owner) == tg_id


class PanelOwnerMiddleware(BaseMiddleware):
    """Prevent users from clicking other users' panel buttons in group chats."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and event.message:
            chat_type = event.message.chat.type
            if chat_type in ("group", "supergroup"):
                allowed = await check_panel_owner(
                    event.message.chat.id,
                    event.message.message_id,
                    event.from_user.id,
                )
                if not allowed:
                    await event.answer(
                        "⚠️ 这不是你的面板，请自己发送命令操作",
                        show_alert=True,
                    )
                    return None
        return await handler(event, data)

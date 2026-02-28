"""Panel ownership middleware — ensures only the invoker can interact with a panel."""

from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery


class PanelOwnerMiddleware(BaseMiddleware):
    """Outer middleware for callback queries.

    Convention: callback_data may end with ``|<tg_id>`` to mark panel ownership.
    If the suffix is present and the caller's tg_id does not match, the callback
    is rejected with an alert.  The suffix is stripped before passing to handlers
    so that existing parsers continue to work unchanged.
    """

    async def __call__(self, handler, event: CallbackQuery, data: dict):
        raw = event.data
        if raw and "|" in raw:
            parts = raw.rsplit("|", 1)
            try:
                owner_id = int(parts[1])
            except ValueError:
                # Not a valid tg_id suffix — pass through unchanged
                return await handler(event, data)

            if event.from_user.id != owner_id:
                await event.answer("\u26a0\ufe0f 这不是你的面板", show_alert=True)
                return

            # Strip the ownership suffix so downstream handlers see clean data
            event.data = parts[0]

        return await handler(event, data)

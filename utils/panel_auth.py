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

            # Strip ownership suffix for downstream filters/handlers.
            # Do not mutate event in place: Telegram objects may be immutable.
            clean_data = parts[0]
            try:
                event = event.model_copy(update={"data": clean_data})
            except Exception:
                # Fallback for mutable model variants.
                event.data = clean_data

        return await handler(event, data)

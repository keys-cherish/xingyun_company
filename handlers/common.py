"""Common handler utilities: group-only filter, middleware."""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import BaseFilter

from config import settings

router = Router()


class GroupOnlyFilter(BaseFilter):
    """Filter that allows a command only in group chats / specific subchannels.

    /company is exempt and works in private chat too.
    """

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        if isinstance(event, types.CallbackQuery):
            chat = event.message.chat if event.message else None
        else:
            chat = event.chat

        if chat is None:
            return False

        # Allow group / supergroup / channel
        if chat.type in ("group", "supergroup"):
            allowed = settings.allowed_chat_id_set
            if allowed and chat.id not in allowed:
                return False
            return True

        return False


class PrivateOnlyCompanyFilter(BaseFilter):
    """In private chat, only /company is allowed."""

    async def __call__(self, event: types.Message) -> bool:
        if event.chat.type == "private":
            return True
        return False


group_only = GroupOnlyFilter()


async def reject_private(message: types.Message):
    """Send a rejection message when user tries non-company commands in private."""
    await message.answer("⚠️ 此命令仅限在群组指定频道中使用。\n私聊仅支持 /company 查看公司信息。")

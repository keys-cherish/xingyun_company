"""Common handler utilities: admin auth, group-only filter, privacy control."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router, types
from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from cache.redis_client import get_redis
from config import settings

router = Router()
logger = logging.getLogger(__name__)
CHANNEL_ONLY_HINT = "❌ 本 bot 仅在指定话题频道内提供服务。"

# ---- 管理员认证 ----
# 已认证管理员存Redis: admin_auth:{tg_id} = "1"

async def is_admin_authenticated(tg_id: int) -> bool:
    """检查用户是否已通过管理员认证（密钥+ID双重验证）。"""
    # 先检查是否在白名单ID中
    if settings.admin_tg_ids.strip():
        allowed = {int(x.strip()) for x in settings.admin_tg_ids.split(",") if x.strip()}
        if tg_id not in allowed:
            return False
    else:
        return False  # 未配置管理员ID列表则无人可成为管理员

    r = await get_redis()
    val = await r.get(f"admin_auth:{tg_id}")
    return val is not None


def is_super_admin(tg_id: int) -> bool:
    """Strict super-admin check for high-risk commands."""
    return tg_id in settings.super_admin_tg_id_set


async def authenticate_admin(tg_id: int, secret_key: str) -> tuple[bool, str]:
    """尝试认证管理员。需要同时满足：TG ID在白名单 + 密钥正确。"""
    # 检查ID白名单
    if settings.admin_tg_ids.strip():
        allowed = {int(x.strip()) for x in settings.admin_tg_ids.split(",") if x.strip()}
        if tg_id not in allowed:
            return False, "无权限"
    else:
        return False, "未配置管理员"

    # 检查密钥
    if not settings.admin_secret_key:
        return False, "未配置管理员密钥"
    if secret_key != settings.admin_secret_key:
        return False, "密钥错误"

    # 认证通过，存入Redis（不设过期，重启bot后需重新认证）
    r = await get_redis()
    await r.set(f"admin_auth:{tg_id}", "1")
    return True, "管理员认证成功"


async def revoke_admin(tg_id: int):
    """撤销管理员认证。"""
    r = await get_redis()
    await r.delete(f"admin_auth:{tg_id}")


# ---- 过滤器 ----


def is_allowed_group_chat(chat: types.Chat) -> bool:
    """Whether this chat is one of the configured command groups."""
    if chat.type not in ("group", "supergroup"):
        return False

    allowed_ids = settings.allowed_chat_id_set
    allowed_usernames = settings.allowed_chat_username_set

    # Backward compatible: if no restriction is configured, allow all groups.
    if not allowed_ids and not allowed_usernames:
        return True

    if allowed_ids and chat.id in allowed_ids:
        return True

    username = (chat.username or "").lstrip("@").lower()
    if allowed_usernames and username in allowed_usernames:
        return True

    return False


def _extract_chat_and_thread(
    event: types.Message | types.CallbackQuery,
) -> tuple[types.Chat | None, int | None]:
    if isinstance(event, types.CallbackQuery):
        if not event.message:
            return None, None
        thread_id = getattr(event.message, "message_thread_id", None)
        return event.message.chat, thread_id

    return event.chat, event.message_thread_id


def is_allowed_topic_thread(thread_id: int | None) -> bool:
    """Whether the topic thread is allowed; empty config means unrestricted."""
    allowed_topics = settings.allowed_topic_thread_id_set
    if not allowed_topics:
        return True
    return thread_id in allowed_topics


def is_allowed_scope(event: types.Message | types.CallbackQuery) -> bool:
    """Check group/channel and topic scope together."""
    chat, thread_id = _extract_chat_and_thread(event)
    if chat is None:
        return False
    if not is_allowed_group_chat(chat):
        return False
    return is_allowed_topic_thread(thread_id)


async def _notify_channel_only(event: types.Message | types.CallbackQuery):
    try:
        if isinstance(event, types.CallbackQuery):
            await event.answer(CHANNEL_ONLY_HINT, show_alert=True)
        else:
            await event.answer(CHANNEL_ONLY_HINT)
    except Exception:
        # Keep filter behavior stable even if Telegram refuses late replies.
        pass

class GroupOnlyFilter(BaseFilter):
    """Only allow commands in configured group/topic scope."""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        return is_allowed_scope(event)


class AdminOnlyFilter(BaseFilter):
    """仅已认证管理员可用。"""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        tg_id = event.from_user.id
        return await is_admin_authenticated(tg_id)


class SuperAdminOnlyFilter(BaseFilter):
    """Only allow the designated super-admin account."""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        return is_super_admin(event.from_user.id)


class GroupScopeMiddleware(BaseMiddleware):
    """Hard-block all message/callback events outside configured scope."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, (types.Message, types.CallbackQuery)):
            if not is_allowed_scope(event):
                chat, thread_id = _extract_chat_and_thread(event)
                logger.info(
                    "Blocked out-of-scope command: chat_id=%s username=%s thread_id=%s",
                    getattr(chat, "id", None),
                    getattr(chat, "username", None),
                    thread_id,
                )
                await _notify_channel_only(event)
                return None
        return await handler(event, data)


group_only = GroupOnlyFilter()
admin_only = AdminOnlyFilter()
super_admin_only = SuperAdminOnlyFilter()
group_scope_middleware = GroupScopeMiddleware()


async def reject_private(message: types.Message):
    """非管理员私聊时的拒绝提示。"""
    await message.answer("此命令仅限在群组指定频道中使用。\n私聊仅支持 /create_company 和 /company 查看信息。")

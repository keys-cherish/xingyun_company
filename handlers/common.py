"""Common handler utilities: admin auth, group-only filter, privacy control."""

from __future__ import annotations

from aiogram import Router, types
from aiogram.filters import BaseFilter

from cache.redis_client import get_redis
from config import settings

router = Router()
SUPER_ADMIN_TG_ID = 5222591634

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
    return tg_id == SUPER_ADMIN_TG_ID


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

class GroupOnlyFilter(BaseFilter):
    """只允许群组中使用，已认证管理员的私聊也放行。"""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        if isinstance(event, types.CallbackQuery):
            chat = event.message.chat if event.message else None
            tg_id = event.from_user.id
        else:
            chat = event.chat
            tg_id = event.from_user.id

        if chat is None:
            return False

        # 群组/超级群组：检查是否在允许列表
        if chat.type in ("group", "supergroup"):
            allowed = settings.allowed_chat_id_set
            if allowed and chat.id not in allowed:
                return False
            return True

        # 私聊：仅已认证管理员放行
        if chat.type == "private":
            return await is_admin_authenticated(tg_id)

        return False


class AdminOnlyFilter(BaseFilter):
    """仅已认证管理员可用。"""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        tg_id = event.from_user.id
        return await is_admin_authenticated(tg_id)


class SuperAdminOnlyFilter(BaseFilter):
    """Only allow the designated super-admin account."""

    async def __call__(self, event: types.Message | types.CallbackQuery) -> bool:
        return is_super_admin(event.from_user.id)


group_only = GroupOnlyFilter()
admin_only = AdminOnlyFilter()
super_admin_only = SuperAdminOnlyFilter()


async def reject_private(message: types.Message):
    """非管理员私聊时的拒绝提示。"""
    await message.answer("此命令仅限在群组指定频道中使用。\n私聊仅支持 /create_company 和 /company 查看信息。")

"""Battle handler – reply to someone with /battle to auto-PK."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command

from aiogram import types

from db.engine import async_session
from services.battle_service import battle

router = Router()


@router.message(Command("battle"))
async def cmd_battle(message: types.Message):
    """Initiate a business battle by replying to someone's message."""
    if not message.reply_to_message:
        await message.answer(
            "⚔️ 使用方法: 回复某人的消息并发送 /battle\n"
            "将对该玩家发起商战!"
        )
        return

    target = message.reply_to_message.from_user
    if not target or target.is_bot:
        await message.answer("❌ 不能对机器人发起商战")
        return

    attacker_tg_id = message.from_user.id
    defender_tg_id = target.id

    async with async_session() as session:
        async with session.begin():
            ok, msg = await battle(session, attacker_tg_id, defender_tg_id)

    await message.answer(msg)

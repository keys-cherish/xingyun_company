"""机器人入口文件。"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from db.engine import init_db
from cache.redis_client import close_redis
from scheduler.daily_settlement import set_bot, start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _register_routers(dp: Dispatcher):
    from handlers.start import router as start_router
    from handlers.company import router as company_router
    from handlers.shareholder import router as shareholder_router
    from handlers.research import router as research_router
    from handlers.product import router as product_router
    from handlers.roadshow import router as roadshow_router
    from handlers.cooperation import router as cooperation_router
    from handlers.realestate import router as realestate_router
    from handlers.dividend import router as dividend_router
    from handlers.ad import router as ad_router
    from handlers.ai_rd import router as ai_rd_router
    from handlers.admin import router as admin_router
    from handlers.exchange import router as exchange_router
    from handlers.battle import router as battle_router
    from handlers.quest import router as quest_router

    dp.include_router(start_router)
    dp.include_router(company_router)
    dp.include_router(shareholder_router)
    dp.include_router(research_router)
    dp.include_router(product_router)
    dp.include_router(roadshow_router)
    dp.include_router(cooperation_router)
    dp.include_router(realestate_router)
    dp.include_router(dividend_router)
    dp.include_router(ad_router)
    dp.include_router(ai_rd_router)
    dp.include_router(admin_router)
    dp.include_router(exchange_router)
    dp.include_router(battle_router)
    dp.include_router(quest_router)


async def main():
    if not settings.bot_token:
        logger.error("BOT_TOKEN 未设置。请创建 .env 文件并填入 BOT_TOKEN=你的token")
        return

    bot = Bot(
        token=settings.bot_token,
        session=AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None,
        default=DefaultBotProperties(parse_mode=None),
    )
    dp = Dispatcher(storage=MemoryStorage())
    from handlers.common import group_scope_middleware
    dp.message.middleware(group_scope_middleware)
    dp.callback_query.middleware(group_scope_middleware)

    # 初始化数据库
    await init_db()
    logger.info("数据库初始化完成")

    # 注册处理器
    _register_routers(dp)

    # 注册限流中间件
    from utils.throttle import ThrottleMiddleware
    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())

    # 注册面板权限中间件（群组中禁止点击他人面板）
    from utils.panel_owner import PanelOwnerMiddleware
    dp.callback_query.middleware(PanelOwnerMiddleware())

    # 注册Bot命令列表（Telegram输入框命令提示）
    from handlers.start import BOT_COMMANDS
    await bot.set_my_commands(BOT_COMMANDS)

    # 启动定时任务
    set_bot(bot)
    start_scheduler()

    # 启动轮询
    logger.info("机器人启动中...")
    try:
        await dp.start_polling(bot)
    finally:
        stop_scheduler()
        await close_redis()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

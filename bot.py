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

    # 私聊兜底：非管理员只允许/start和/company，管理员放行所有
    from handlers.common import reject_private, is_admin_authenticated
    from aiogram import F, Router
    from aiogram.fsm.context import FSMContext
    fallback = Router()

    @fallback.message(F.chat.type == "private")
    async def _private_fallback(message, state: FSMContext):
        # 如果用户在FSM状态中（如合作输入公司ID），放行
        current_state = await state.get_state()
        if current_state is not None:
            return
        if message.text and message.text.startswith("/company"):
            return
        if message.text and message.text.startswith("/list_company"):
            return
        if message.text and message.text.startswith("/member"):
            return
        if message.text and message.text.startswith("/start"):
            return
        if message.text and message.text.startswith("/create_company"):
            return
        if message.text and message.text.startswith("/admin"):
            return
        if message.text and message.text.startswith("/help"):
            return
        if message.text and message.text.startswith("/battle"):
            return
        if message.text and message.text.startswith("/cooperate"):
            return
        if message.text and message.text.startswith("/new_product"):
            return
        if message.text and message.text.startswith("/dissolve"):
            return
        if message.text and message.text.startswith("/clear_product"):
            return
        if message.text and message.text.startswith("/rank_company"):
            return
        if message.text and message.text.startswith("/makeup"):
            return
        if message.text and message.text.startswith("/give_money"):
            return
        if message.text and message.text.startswith("/welfare"):
            return
        if message.text and message.text.startswith("/quest"):
            return
        if message.text and message.text.startswith("/cleanup"):
            return
        # 已认证管理员放行
        if await is_admin_authenticated(message.from_user.id):
            return
        await reject_private(message)

    dp.include_router(fallback)


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

    # 初始化数据库
    await init_db()
    logger.info("数据库初始化完成")

    # 注册处理器
    _register_routers(dp)

    # 注册限流中间件
    from utils.throttle import ThrottleMiddleware
    from utils.topic_gate import TopicGateMiddleware
    dp.message.middleware(TopicGateMiddleware())
    dp.callback_query.middleware(TopicGateMiddleware())
    dp.message.middleware(ThrottleMiddleware())
    dp.callback_query.middleware(ThrottleMiddleware())

    # 注册面板权限中间件（outer，在路由匹配前执行）
    from utils.panel_auth import PanelOwnerMiddleware
    dp.callback_query.outer_middleware(PanelOwnerMiddleware())

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

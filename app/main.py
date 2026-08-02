from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.config import BASE_DIR, load_settings
from app.database import Database
from app.handlers import build_router
from app.services.kimi import KimiService


async def main() -> None:
    settings = load_settings()
    db = Database(settings.database_path)
    await db.initialize()

    kimi = KimiService(settings.ai_api_key, settings.ai_model, settings.ai_base_url)
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(settings, db, kimi, BASE_DIR / "exports"))

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть главное меню"),
            BotCommand(command="cancel", description="Отменить текущее действие"),
            BotCommand(command="myid", description="Показать Telegram ID"),
        ]
    )

    logging.info("ФД Финансы запущен. Kimi: %s", "ON" if kimi.enabled else "OFF")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

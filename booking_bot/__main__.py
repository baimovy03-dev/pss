import argparse
import asyncio
import logging
import os

from aiogram import Bot
from aiogram.types import BotCommand
from dotenv import load_dotenv

from .app import build_dispatcher
from .config import load_config
from .storage import BookingStore


async def main(check=False):
    if os.getenv('LOAD_DOTENV', '1') != '0':
        load_dotenv(dotenv_path='.env')
    path = os.getenv('CONFIG_PATH', 'config.yaml')
    config = load_config(path)
    store = BookingStore(os.getenv('DATABASE_PATH', 'data/bookings.sqlite3'))
    try:
        admins = {int(value.strip()) for value in os.getenv('ADMIN_IDS', '').split(',') if value.strip()}
    except ValueError:
        raise SystemExit('ADMIN_IDS должен содержать числовые Telegram ID через запятую.') from None
    dp, reminders = build_dispatcher(config, store, admins, path)
    if check:
        print('Настройки, база SQLite и обработчики проверены. Запросы к Telegram не выполнялись.')
        await dp.storage.close()
        return
    token = os.getenv('BOT_TOKEN')
    if not token:
        raise SystemExit('Укажите BOT_TOKEN в .env или окружении. См. README.md.')
    if not admins:
        logging.warning('ADMIN_IDS пуст: админ-меню недоступно. Свой ID можно узнать командой /id.')
    bot = Bot(token)
    task = None
    try:
        await bot.set_my_commands([BotCommand(command='menu', description='Главное меню'),
                                   BotCommand(command='mine', description='Мои записи'),
                                   BotCommand(command='id', description='Мой Telegram ID'),
                                   BotCommand(command='admin', description='Администратор')])
        task = asyncio.create_task(reminders(bot))
        await dp.start_polling(bot, close_bot_session=False)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()
        await dp.storage.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Проверка конфигурации без Telegram')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    asyncio.run(main(args.check))

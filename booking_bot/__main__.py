import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from .storage import BookingStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def main():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise SystemExit('Задайте BOT_TOKEN в окружении (см. README.md).')
    tz = ZoneInfo(os.getenv('BOT_TIMEZONE', 'Asia/Bishkek'))
    store = BookingStore(os.getenv('DATABASE_PATH', 'data/bookings.sqlite3'))
    bot = Bot(token)
    dp = Dispatcher()

    def local_time(value):
        return datetime.fromisoformat(value).astimezone(tz).strftime('%d.%m.%Y %H:%M')

    @dp.message(CommandStart())
    async def start(message: Message):
        await message.answer(
            'Демо записи на приём. Все часы указаны в ' + str(tz) + '.\n'
            '/book 2026-12-01 14:00 — записаться\n'
            '/mine — мои записи\n/cancel НОМЕР — отменить запись\n/faq — частые вопросы\n'
            'Приём ежедневно с 09:00 до 18:00, длительность — один час.')

    @dp.message(Command('book'))
    async def book(message: Message):
        try:
            slot = datetime.strptime(message.text.split(maxsplit=1)[1], '%Y-%m-%d %H:%M').replace(tzinfo=tz)
            if slot.minute != 0 or not 9 <= slot.hour < 18:
                raise ValueError('Выберите начало часа с 09:00 до 17:00.')
            store.reserve(message.from_user.id, slot)
        except (IndexError, ValueError) as exc:
            await message.answer(str(exc) if isinstance(exc, ValueError) and str(exc).startswith(('Выберите', 'Это'))
                                 else 'Формат: /book ГГГГ-ММ-ДД ЧЧ:00')
            return
        await message.answer('Запись подтверждена: ' + slot.strftime('%d.%m.%Y %H:%M'))

    @dp.message(Command('mine'))
    async def mine(message: Message):
        rows = store.upcoming(message.from_user.id)
        await message.answer('\n'.join(f'№{key}: {local_time(slot)}' for key, slot in rows) or 'У вас нет будущих записей.')

    @dp.message(Command('cancel'))
    async def cancel(message: Message):
        try:
            key = int(message.text.split(maxsplit=1)[1])
        except (IndexError, ValueError):
            await message.answer('Формат: /cancel НОМЕР. Номер можно узнать через /mine.')
            return
        removed = store.cancel(message.from_user.id, key)
        await message.answer('Запись отменена.' if removed else 'Ваша запись с таким номером не найдена.')

    @dp.message(Command('faq'))
    async def faq(message: Message):
        await message.answer('Часы: ежедневно 09:00–18:00. Приём: один час.\n'
                             'Отмена: /cancel НОМЕР. Напоминание придёт в течение часа перед визитом.\n'
                             'Это демонстрация; адрес, услуги и цены нужно добавить перед использованием.')

    @dp.message(F.text)
    async def fallback(message: Message):
        await message.answer('Список команд: /start')

    async def reminders():
        while True:
            try:
                for key, user_id, slot in store.due():
                    try:
                        await bot.send_message(user_id, 'Напоминание о визите: ' + local_time(slot))
                    except TelegramForbiddenError:
                        store.mark_reminded(key)
                    except TelegramAPIError:
                        log.warning('Не удалось отправить напоминание для записи %s', key)
                    else:
                        store.mark_reminded(key)
            except Exception:
                log.exception('Ошибка цикла напоминаний')
            await asyncio.sleep(30)

    task = asyncio.create_task(reminders())
    try:
        await dp.start_polling(bot)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bot.session.close()


if __name__ == '__main__':
    asyncio.run(main())

import asyncio
import tempfile
from unittest.mock import patch
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Message, Update

from booking_bot.app import build_dispatcher
from booking_bot.config import load_config
from booking_bot.scheduling import Scheduler
from booking_bot.storage import BookingStore


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        return Message(message_id=len(self.sent), date=datetime.now(timezone.utc), chat={'id': int(method.chat_id), 'type': 'private'}, text=getattr(method, 'text', None))

    async def stream_content(self, *args, **kwargs):
        yield b''


class BotFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = load_config()
        self.store = BookingStore(Path(self.temp.name) / 'db.sqlite3')
        self.dp, self.reminders = build_dispatcher(self.cfg, self.store, {999})
        self.session = FakeSession()
        self.bot = Bot('123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi', session=self.session)
        self.seq = 0

    async def asyncTearDown(self):
        await self.dp.storage.close()
        await self.bot.session.close()

    async def send(self, text=None, callback=None, user=123):
        self.seq += 1
        actor = {'id': user, 'is_bot': False, 'first_name': 'Иван'}
        msg = {'message_id': self.seq, 'date': int(datetime.now().timestamp()), 'chat': {'id': user, 'type': 'private'}, 'from': actor, 'text': text or 'Меню'}
        value = {'update_id': self.seq}
        if callback:
            value['callback_query'] = {'id': str(self.seq), 'from': actor, 'chat_instance': 'demo', 'data': callback, 'message': msg}
        else:
            value['message'] = msg
        await self.dp.feed_update(self.bot, Update.model_validate(value))

    def texts(self):
        return '\n'.join(m.text for m in self.session.sent if isinstance(m, SendMessage))

    async def select(self, invalid_contact=False):
        await self.send(callback='book')
        await self.send(callback='service:haircut')
        await self.send(callback='staff:anna')
        scheduler = Scheduler(self.cfg, self.store)
        for offset in range(14):
            day = datetime.now(scheduler.tz).date() + timedelta(days=offset)
            slots = scheduler.slots('haircut', 'anna', day)
            if slots:
                slot = slots[-1]
                break
        else:
            self.fail('No configured slots')
        await self.send(callback='day:' + slot.date().isoformat())
        await self.send(callback=f'time:{int(slot.timestamp())}')
        await self.send(text='Иван')
        if invalid_contact:
            await self.send(text='не телефон')
            self.assertIn('Введите телефон:', self.texts())
            self.assertEqual(self.store.upcoming(), [])
        await self.send(text='+996 555 123456')
        return slot

    async def test_full_booking_confirm_and_admin_notification(self):
        await self.send(text='/start')
        await self.select()
        await self.send(callback='confirm')
        rows = self.store.upcoming(123)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['contact'], '+996555123456')
        self.assertIn('Запись подтверждена', self.texts())
        self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == 999 and 'Новая запись' in m.text for m in self.session.sent))
        await self.send(callback='confirm')
        self.assertEqual(len(self.store.upcoming(123)), 1)
        await self.send(callback='mine')
        self.assertIn('Стоимость:', self.texts())

    async def test_admin_access_and_foreign_cancel_denied(self):
        await self.select()
        await self.send(callback='confirm')
        key = self.store.upcoming(123)[0]['id']
        await self.send(callback='admin', user=456)
        self.assertIn('Доступ только для администратора', self.texts())
        await self.send(callback=f'cancel_yes:{key}', user=456)
        self.assertEqual(self.store.get(key)['status'], 'active')
        await self.send(callback='export', user=456)
        self.assertFalse(any(type(m).__name__ == 'SendDocument' for m in self.session.sent))
        await self.send(callback=f'admin_cancel_yes:{key}', user=999)
        self.assertEqual(self.store.get(key)['status'], 'cancelled')

    async def test_stale_button_and_invalid_contact(self):
        await self.send(callback='time:1234567890')
        self.assertIn('кнопка устарела', self.texts())
        await self.select(invalid_contact=True)
        # Confirmation cannot be submitted by a different user/session.
        await self.send(callback='confirm', user=456)
        self.assertEqual(self.store.upcoming(), [])

    async def test_closure_commands_authorized(self):
        await self.send(text='/close 2030-01-01', user=456)
        self.assertEqual(self.store.closed_dates(), set())
        await self.send(text='/close 2030-01-01', user=999)
        self.assertEqual(self.store.closed_dates(), {'2030-01-01'})
        await self.send(text='/open 2030-01-01', user=999)
        self.assertEqual(self.store.closed_dates(), set())

    async def test_reminder_delivered_once(self):
        now = datetime.now(timezone.utc)
        service = self.cfg['services'][0]
        staff = self.cfg['staff'][0]
        key = self.store.reserve(123, 'Иван', '1234567', service, staff, now + timedelta(minutes=30))
        with patch('booking_bot.app.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.reminders(self.bot)
        self.assertEqual(self.store.get(key)['reminded'], 1)
        sent = [m for m in self.session.sent if isinstance(m, SendMessage) and 'Напоминаем' in m.text]
        self.assertEqual(len(sent), 1)
        with patch('booking_bot.app.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.reminders(self.bot)
        self.assertEqual(len([m for m in self.session.sent if isinstance(m, SendMessage) and 'Напоминаем' in m.text]), 1)

    async def test_admin_export_and_agenda(self):
        await self.select()
        await self.send(callback='confirm')
        await self.send(callback='agenda:all:0', user=999)
        self.assertIn('Контакт: +996555123456', self.texts())
        await self.send(callback='export', user=999)
        exports = [m for m in self.session.sent if type(m).__name__ == 'SendDocument']
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0].chat_id, 999)
        content = exports[0].document.data.decode('utf-8-sig')
        self.assertIn("'+996555123456", content)

    async def test_price_changed_before_confirm_requires_new_booking(self):
        await self.select()
        self.cfg['services'][0]['price'] += 100
        await self.send(callback='confirm')
        self.assertEqual(self.store.upcoming(), [])
        self.assertIn('Условия записи изменились', self.texts())

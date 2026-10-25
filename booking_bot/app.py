import asyncio
import csv
from copy import deepcopy
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (BufferedInputFile, CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, KeyboardButton, Message,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

from .config import load_config
from .scheduling import Scheduler

log = logging.getLogger(__name__)


class Booking(StatesGroup):
    choosing = State()
    name = State()
    contact = State()
    confirm = State()


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows])


def build_dispatcher(config, store, admin_ids=(), config_path='config.yaml'):
    dp = Dispatcher(storage=MemoryStorage())
    router = Router()
    router.message.filter(F.chat.type == 'private')
    router.callback_query.filter(F.message.chat.type == 'private')
    dp.include_router(router)
    cfg = config
    scheduler = Scheduler(cfg, store)
    admins = set(admin_ids)

    def menu(user_id):
        rows = [[('📅 Записаться', 'book')], [('🗓 Мои записи', 'mine')],
                [('💬 Частые вопросы', 'faq'), ('📍 Контакты', 'contacts')]]
        if user_id in admins:
            rows.append([('⚙️ Администратор', 'admin')])
        return keyboard(rows)

    def back():
        return keyboard([[('🏠 Главное меню', 'home')]])

    def stamp(epoch):
        return datetime.fromtimestamp(epoch, scheduler.tz).strftime('%d.%m.%Y %H:%M')

    def summary(row, admin=False):
        text = (f"Запись №{row['id']}\n{row['service_name']} · {row['staff_name']}\n"
                f"{stamp(row['start'])}–{datetime.fromtimestamp(row['end'], scheduler.tz):%H:%M}\n"
                f"Стоимость: {row['price']} {cfg['business']['currency']}")
        if admin:
            text += f"\nКлиент: {row['client_name']}\nКонтакт: {row['contact']}\nTelegram ID: {row['user_id']}"
        return text

    async def notify(bot, text):
        for user_id in admins:
            try:
                await bot.send_message(user_id, text)
            except TelegramAPIError:
                log.warning('Не доставлено уведомление администратору %s', user_id)

    @router.message(CommandStart())
    @router.message(Command('menu'))
    async def welcome(message: Message, state: FSMContext):
        await state.clear()
        await message.answer('Главное меню', reply_markup=ReplyKeyboardRemove())
        await message.answer(f"{cfg['business']['name']}\n\n{cfg['business']['welcome']}\n"
                             f"Часовой пояс: {cfg['business']['timezone']}", reply_markup=menu(message.from_user.id))

    @router.message(Command('id'))
    async def identity(message: Message):
        await message.answer(f'Ваш Telegram ID: {message.from_user.id}')

    @router.message(Command('admin'))
    async def admin_command(message: Message):
        if message.from_user.id not in admins:
            await message.answer('Доступ только для администратора.')
            return
        await show_admin(message)

    async def show_admin(message):
        await message.answer('Управление записью\n'
                             '/close ГГГГ-ММ-ДД — закрыть день\n/open ГГГГ-ММ-ДД — открыть день, закрытый через /close\n'
                             '/reload — перечитать config.yaml\n'
                             'Закрытие дня не отменяет уже созданные записи.\n'
                             'Услуги, сотрудники, цены, расписание и FAQ меняются в config.yaml.',
                             reply_markup=keyboard([[('Сегодня', 'agenda:0:0'), ('Завтра', 'agenda:1:0')],
                                                    [('Все будущие записи', 'agenda:all:0')],
                                                    [('📊 Статистика', 'stats'), ('📄 Экспорт CSV', 'export')],
                                                    [('🏠 Меню', 'home')]]))

    @router.message(Command('close', 'open'))
    async def closure(message: Message):
        if message.from_user.id not in admins:
            await message.answer('Доступ только для администратора.')
            return
        try:
            command, value = message.text.split(maxsplit=1)
            day = date.fromisoformat(value.strip()).isoformat()
        except ValueError:
            await message.answer('Укажите дату: /close ГГГГ-ММ-ДД или /open ГГГГ-ММ-ДД')
            return
        closed = command.split('@')[0] == '/close'
        store.set_closed(day, closed)
        await message.answer(f'{day}: ' + ('новые записи закрыты.' if closed else 'ручная блокировка снята; расписание и config.yaml продолжают действовать.'))

    @router.message(Command('reload'))
    async def reload_config(message: Message):
        nonlocal cfg, scheduler
        if message.from_user.id not in admins:
            await message.answer('Доступ только для администратора.')
            return
        try:
            updated = load_config(config_path)
        except (ValueError, OSError) as exc:
            await message.answer(f'Настройки не изменены: {str(exc)[:1000]}')
            return
        cfg = updated
        scheduler = Scheduler(cfg, store)
        await message.answer('Настройки обновлены. Существующие записи сохранены. Начатая запись проверяется заново при подтверждении.')

    async def show_mine(message, user_id):
        rows = store.upcoming(user_id)
        if not rows:
            await message.answer('У вас пока нет будущих записей.', reply_markup=back())
        for row in rows:
            await message.answer(summary(row), reply_markup=keyboard([[('Отменить запись', f"cancel:{row['id']}")], [('🏠 Меню', 'home')]]))

    @router.message(Command('mine'))
    async def mine_command(message: Message):
        await show_mine(message, message.from_user.id)

    @router.message(Booking.name)
    async def receive_name(message: Message, state: FSMContext):
        name = (message.text or '').strip()
        if not 2 <= len(name) <= 80:
            await message.answer('Введите имя текстом: от 2 до 80 символов. /menu — выйти.')
            return
        await state.update_data(name=name)
        await state.set_state(Booking.contact)
        await message.answer('Отправьте свой номер кнопкой или введите телефон в международном формате (+996…).\n'
                             'Контакт увидит администратор для связи по записи. /menu — выйти.',
                             reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='📱 Поделиться моим номером', request_contact=True)]], resize_keyboard=True, one_time_keyboard=True))

    @router.message(Booking.contact)
    async def receive_contact(message: Message, state: FSMContext):
        if message.contact and message.contact.user_id != message.from_user.id:
            await message.answer('Отправьте собственный контакт или введите номер текстом.')
            return
        phone = message.contact.phone_number if message.contact else (message.text or '')
        phone = re.sub(r'[\s()\-]', '', phone)
        if not re.fullmatch(r'\+?\d{7,15}', phone):
            await message.answer('Введите телефон: 7–15 цифр, можно с + в начале.')
            return
        await state.update_data(contact=phone)
        data = await state.get_data()
        service = next((s for s in cfg['services'] if s['id'] == data.get('service')), None)
        person = next((s for s in cfg['staff'] if s['id'] == data.get('staff')), None)
        if not service or not person:
            await state.clear()
            await message.answer('Настройки изменились. Начните запись заново.', reply_markup=ReplyKeyboardRemove())
            await message.answer('Меню', reply_markup=menu(message.from_user.id))
            return
        await state.update_data(offer=deepcopy({'service': service, 'staff': person, 'business': cfg['business'],
                                                    'cancel_notice': cfg['booking']['cancel_notice_minutes']}))
        await state.set_state(Booking.confirm)
        await message.answer('Проверьте данные', reply_markup=ReplyKeyboardRemove())
        await message.answer(f"{service['name']} · {person['name']}\n{stamp(data['start'])}\n"
                             f"{service['duration_minutes']} мин · {service['price']} {cfg['business']['currency']}\n"
                             f"Имя: {data['name']}\nТелефон: {phone}\nАдрес: {cfg['business']['address']}\n"
                             f"Отмена не позднее чем за {cfg['booking']['cancel_notice_minutes']} мин.",
                             reply_markup=keyboard([[('✅ Подтвердить запись', 'confirm')], [('Отмена', 'home')]]))

    @router.callback_query()
    async def callbacks(call: CallbackQuery, state: FSMContext):
        await call.answer()
        action = call.data or ''
        message = call.message
        user_id = call.from_user.id
        if action == 'home':
            await state.clear()
            await message.answer('Главное меню', reply_markup=ReplyKeyboardRemove())
            await message.answer(cfg['business']['name'], reply_markup=menu(user_id))
        elif action == 'contacts':
            await message.answer(f"{cfg['business']['address']}\n{cfg['business']['contacts']}\nЧасовой пояс: {cfg['business']['timezone']}", reply_markup=back())
        elif action == 'faq':
            await message.answer('Частые вопросы', reply_markup=keyboard([[(q['question'], f'faq:{i}')] for i, q in enumerate(cfg['faq'])] + [[('🏠 Меню', 'home')]]))
        elif action.startswith('faq:'):
            try:
                q = cfg['faq'][int(action.split(':')[1])]
                await message.answer(q['answer'], reply_markup=back())
            except (ValueError, IndexError):
                await message.answer('Список вопросов изменился. Откройте FAQ заново.', reply_markup=back())
        elif action == 'mine':
            await show_mine(message, user_id)
        elif action.startswith(('cancel:', 'cancel_yes:', 'admin_cancel:', 'admin_cancel_yes:')):
            admin = action.startswith('admin_')
            if admin and user_id not in admins:
                return
            try:
                key = int(action.split(':')[1])
                row = store.get(key)
                if not row or row['status'] != 'active' or (not admin and row['user_id'] != user_id):
                    raise ValueError('Запись не найдена.')
                if '_yes:' not in action:
                    prefix = 'admin_cancel_yes' if admin else 'cancel_yes'
                    await message.answer('Отменить?\n' + summary(row), reply_markup=keyboard([[('Да, отменить', f'{prefix}:{key}')], [('Нет, в меню', 'home')]]))
                    return
                row = store.cancel(user_id, key, cfg['booking']['cancel_notice_minutes'], admin)
            except ValueError as exc:
                await message.answer(str(exc), reply_markup=back())
                return
            await message.answer('Запись отменена.', reply_markup=back())
            if admin:
                try:
                    await call.bot.send_message(row['user_id'], 'Администратор отменил вашу запись.\n' + summary(row))
                except TelegramAPIError:
                    await message.answer('Не удалось доставить клиенту уведомление; свяжитесь с ним по контакту.')
            await notify(call.bot, '❌ Запись отменена\n' + summary(row, True))
        elif action in ('admin', 'stats', 'export') or action.startswith('agenda:'):
            if user_id not in admins:
                await message.answer('Доступ только для администратора.')
                return
            rows = store.upcoming()
            if action == 'admin':
                await show_admin(message)
            elif action == 'stats':
                today = datetime.now(scheduler.tz).date()
                count = sum(datetime.fromtimestamp(r['start'], scheduler.tz).date() == today for r in rows)
                await message.answer(f'Предстоящих записей: {len(rows)}\nСегодня: {count}\nКлиентов с будущими записями: {len({r["user_id"] for r in rows})}\nЗакрытые вручную даты: ' + (', '.join(sorted(store.closed_dates())) or 'нет'), reply_markup=back())
            elif action == 'export':
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['Номер', 'Дата', 'Услуга', 'Сотрудник', 'Цена', 'Имя', 'Контакт', 'Telegram ID'])
                def safe(value):
                    text = str(value)
                    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text
                for r in rows:
                    writer.writerow([safe(v) for v in [r['id'], stamp(r['start']), r['service_name'], r['staff_name'], r['price'], r['client_name'], r['contact'], r['user_id']]])
                await message.answer_document(BufferedInputFile(output.getvalue().encode('utf-8-sig'), filename='appointments.csv'), caption='Будущие активные записи. Файл содержит персональные данные.')
            else:
                _, day, page = action.split(':')
                page = max(0, int(page))
                if day != 'all':
                    selected = datetime.now(scheduler.tz).date() + timedelta(days=int(day))
                    rows = [r for r in rows if datetime.fromtimestamp(r['start'], scheduler.tz).date() == selected]
                if not rows:
                    await message.answer('Записей нет.', reply_markup=back())
                for row in rows[page * 10:(page + 1) * 10]:
                    await message.answer(summary(row, True), reply_markup=keyboard([[('Отменить', f"admin_cancel:{row['id']}")]]))
                nav = []
                if page:
                    nav.append(('← Назад', f'agenda:{day}:{page-1}'))
                if (page + 1) * 10 < len(rows):
                    nav.append(('Далее →', f'agenda:{day}:{page+1}'))
                await message.answer('Просмотр записей', reply_markup=keyboard(([nav] if nav else []) + [[('Админ-меню', 'admin')]]))
        elif action == 'book':
            await state.clear()
            await state.set_state(Booking.choosing)
            await message.answer('Выберите услугу', reply_markup=keyboard([
                [(f"{s['name']} · {s['duration_minutes']} мин · {s['price']} {cfg['business']['currency']}", f"service:{s['id']}")]
                for s in cfg['services']] + [[('🏠 Меню', 'home')]]))
        elif action.startswith('service:'):
            service_id = action.split(':')[1]
            if not any(s['id'] == service_id for s in cfg['services']):
                await message.answer('Услуга больше недоступна.', reply_markup=back())
                return
            await state.clear()
            await state.set_state(Booking.choosing)
            await state.update_data(service=service_id)
            await message.answer('Выберите специалиста', reply_markup=keyboard([
                [(p['name'], f"staff:{p['id']}")] for p in cfg['staff'] if service_id in p['services']
            ] + [[('← Услуги', 'book')]]))
        elif action.startswith(('staff:', 'days:', 'day:', 'times:', 'time:')):
            data = await state.get_data()
            if await state.get_state() != Booking.choosing.state or 'service' not in data:
                await message.answer('Эта кнопка устарела. Начните запись заново.', reply_markup=back())
                return
            if action.startswith('staff:'):
                person = action.split(':')[1]
                if not any(p['id'] == person and data['service'] in p['services'] for p in cfg['staff']):
                    await message.answer('Специалист недоступен.', reply_markup=back())
                    return
                await state.update_data(staff=person)
                data['staff'] = person
            if 'staff' not in data:
                await message.answer('Сначала выберите специалиста.', reply_markup=back())
                return
            if action.startswith(('staff:', 'days:')):
                page = int(action.split(':')[1]) if action.startswith('days:') else 0
                today = datetime.now(scheduler.tz).date()
                days = []
                for offset in range(page * 7, min((page + 1) * 7, cfg['booking']['days_ahead'])):
                    day = today + timedelta(days=offset)
                    slots = scheduler.slots(data['service'], data['staff'], day)
                    if slots:
                        days.append([(day.strftime('%d.%m') + ' · ' + ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'][day.weekday()], 'day:' + day.isoformat())])
                nav = []
                if page > 0:
                    nav.append(('← Раньше', f'days:{page-1}'))
                if (page + 1) * 7 < cfg['booking']['days_ahead']:
                    nav.append(('Позже →', f'days:{page+1}'))
                await message.answer('Выберите дату' if days else 'На этой неделе свободных дат нет.', reply_markup=keyboard(days + ([nav] if nav else []) + [[('← Специалисты', 'service:' + data['service'])]]))
            elif action.startswith(('day:', 'times:')):
                if action.startswith('day:'):
                    selected, page = action.split(':')[1], 0
                else:
                    _, selected, page = action.split(':')
                    page = int(page)
                slots = scheduler.slots(data['service'], data['staff'], date.fromisoformat(selected))
                buttons = [(slot.strftime('%H:%M'), f'time:{int(slot.timestamp())}') for slot in slots[page * 24:(page + 1) * 24]]
                rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
                nav = []
                if page > 0:
                    nav.append(('← Раньше', f'times:{selected}:{page-1}'))
                if (page + 1) * 24 < len(slots):
                    nav.append(('Позже →', f'times:{selected}:{page+1}'))
                await message.answer(f"{selected} · время {cfg['business']['timezone']}" if slots else 'Свободное время закончилось. Выберите другую дату.', reply_markup=keyboard(rows + ([nav] if nav else []) + [[('← Даты', 'days:0')]]))
            else:
                start = datetime.fromtimestamp(int(action.split(':')[1]), scheduler.tz)
                if start not in scheduler.slots(data['service'], data['staff'], start.date()):
                    await message.answer('Время уже недоступно. Выберите другое.', reply_markup=keyboard([[('Выбрать дату', 'days:0')]]))
                    return
                await state.update_data(start=int(start.timestamp()))
                await state.set_state(Booking.name)
                await message.answer('Как к вам обращаться? Введите имя (2–80 символов). /menu — выйти.')
        elif action == 'confirm':
            if await state.get_state() != Booking.confirm.state:
                await message.answer('Подтверждение уже обработано или устарело.', reply_markup=back())
                return
            data = await state.get_data()
            current_offer = {
                'service': next((s for s in cfg['services'] if s['id'] == data['service']), None),
                'staff': next((s for s in cfg['staff'] if s['id'] == data['staff']), None),
                'business': cfg['business'], 'cancel_notice': cfg['booking']['cancel_notice_minutes']}
            if current_offer != data.get('offer'):
                await state.clear()
                await message.answer('Условия записи изменились. Пожалуйста, выберите услугу заново.',
                                     reply_markup=keyboard([[('Записаться', 'book')]]))
                return
            try:
                key = scheduler.book(user_id, data['name'], data['contact'], data['service'], data['staff'], datetime.fromtimestamp(data['start'], scheduler.tz))
            except ValueError as exc:
                await state.clear()
                await message.answer(str(exc), reply_markup=keyboard([[('Выбрать заново', 'book')], [('Меню', 'home')]]))
                return
            await state.clear()
            row = store.get(key)
            await message.answer('✅ Запись подтверждена!\n' + summary(row) + f"\nАдрес: {cfg['business']['address']}", reply_markup=back())
            await notify(call.bot, '📅 Новая запись\n' + summary(row, True))
        else:
            await message.answer('Откройте актуальное меню: /menu')

    @router.message()
    async def fallback(message: Message):
        await message.answer('Для записи используйте меню. /menu — начать заново.', reply_markup=menu(message.from_user.id))

    async def reminders(bot: Bot):
        while True:
            try:
                for row in store.due(cfg['booking']['reminder_minutes']):
                    try:
                        await bot.send_message(row['user_id'], '🔔 Напоминаем о визите\n' + summary(row) + f"\n{cfg['business']['address']}", reply_markup=back())
                    except TelegramForbiddenError:
                        store.mark_reminded(row['id'])
                    except TelegramAPIError:
                        log.warning('Не доставлено напоминание для записи %s', row['id'])
                    else:
                        store.mark_reminded(row['id'])
            except Exception:
                log.exception('Сбой цикла напоминаний')
            await asyncio.sleep(30)

    return dp, reminders

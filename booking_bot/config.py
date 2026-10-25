import re
from datetime import date, time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


def load_config(path='config.yaml'):
    try:
        cfg = yaml.safe_load(Path(path).read_text())
    except yaml.YAMLError as exc:
        raise ValueError('config.yaml: ошибка синтаксиса YAML') from exc
    if not isinstance(cfg, dict):
        raise ValueError('config.yaml: ожидается словарь настроек')
    try:
        ZoneInfo(cfg['business']['timezone'])
        for key in ('name', 'welcome', 'address', 'contacts', 'currency'):
            if not isinstance(cfg['business'][key], str) or not 1 <= len(cfg['business'][key]) <= 500:
                raise ValueError(f'business.{key}: текст длиной 1–500')
        limits = {'days_ahead': (1, 60), 'slot_step_minutes': (5, 120),
                  'min_notice_minutes': (0, 10080), 'cancel_notice_minutes': (0, 10080),
                  'reminder_minutes': (1, 10080), 'max_active_per_client': (1, 50)}
        for key, (low, high) in limits.items():
            value = cfg['booking'][key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'booking.{key}: целое число {low}–{high}')
        def schedule(value):
            for day, intervals in value.items():
                if type(day) is not int or not 0 <= day <= 6:
                    raise ValueError('День недели должен быть числом 0–6')
                previous = None
                for start, end in intervals:
                    start, end = time.fromisoformat(start), time.fromisoformat(end)
                    if start.tzinfo or end.tzinfo or start.second or end.second or start.microsecond or end.microsecond:
                        raise ValueError('В расписании используйте ЧЧ:ММ без секунд и часового пояса')
                    if start >= end or (previous and start < previous):
                        raise ValueError('Интервалы расписания должны идти по порядку без пересечений')
                    previous = end
        schedule(cfg['schedule'])
        ids = {}
        for section in ('services', 'staff'):
            if not 1 <= len(cfg[section]) <= 30:
                raise ValueError(f'{section}: требуется 1–30 элементов')
            ids[section] = set()
            for item in cfg[section]:
                key = item['id']
                if not re.fullmatch('[a-z0-9_]{1,16}', key) or key in ids[section]:
                    raise ValueError(f'{section}: id должен быть уникальным, a-z/0-9/_, до 16 символов')
                ids[section].add(key)
                if not isinstance(item['name'], str) or not 1 <= len(item['name']) <= 60:
                    raise ValueError('Название: 1–60 символов')
        for item in cfg['services']:
            if type(item['duration_minutes']) is not int or not 5 <= item['duration_minutes'] <= 480:
                raise ValueError('Длительность услуги: 5–480 минут')
            if not isinstance(item['price'], (int, float)) or item['price'] < 0:
                raise ValueError('Цена не может быть отрицательной')
        for person in cfg['staff']:
            if not person['services'] or not set(person['services']) <= ids['services']:
                raise ValueError('У сотрудника указана неизвестная услуга')
            if 'schedule' in person:
                schedule(person['schedule'])
            for day in person.get('closed_dates', []):
                date.fromisoformat(str(day))
        for day in cfg['booking']['closed_dates']:
            date.fromisoformat(str(day))
        if len(cfg['faq']) > 20:
            raise ValueError('Максимум 20 вопросов FAQ')
        for item in cfg['faq']:
            if not 1 <= len(item['question']) <= 80 or not 1 <= len(item['answer']) <= 3000:
                raise ValueError('FAQ: вопрос до 80, ответ до 3000 символов')
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f'Неверная структура config.yaml: {exc}') from exc
    return cfg

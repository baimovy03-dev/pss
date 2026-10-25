from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo


class Scheduler:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.tz = ZoneInfo(config['business']['timezone'])

    def slots(self, service_id, staff_id, day, now=None):
        now = now or datetime.now(timezone.utc)
        cfg = self.config
        service = next((s for s in cfg['services'] if s['id'] == service_id), None)
        staff = next((s for s in cfg['staff'] if s['id'] == staff_id), None)
        if not service or not staff or service_id not in staff['services']:
            return []
        today = now.astimezone(self.tz).date()
        if not today <= day < today + timedelta(days=cfg['booking']['days_ahead']):
            return []
        closed = {str(d) for d in cfg['booking']['closed_dates'] + staff.get('closed_dates', [])} | self.store.closed_dates()
        if day.isoformat() in closed:
            return []
        result = []
        for begin, end in staff.get('schedule', cfg['schedule']).get(day.weekday(), []):
            cursor = datetime.combine(day, time.fromisoformat(begin), self.tz)
            limit = datetime.combine(day, time.fromisoformat(end), self.tz)
            while cursor + timedelta(minutes=service['duration_minutes']) <= limit:
                # Reject nonexistent wall-clock times during daylight-saving transitions.
                valid = cursor.astimezone(timezone.utc).astimezone(self.tz).replace(fold=0) == cursor
                finish = cursor.astimezone(timezone.utc) + timedelta(minutes=service['duration_minutes'])
                if valid and finish.astimezone(self.tz).utcoffset() == cursor.utcoffset() and finish.astimezone(self.tz) <= limit and cursor.timestamp() > (now + timedelta(minutes=cfg['booking']['min_notice_minutes'])).timestamp() and not self.store.busy(staff_id, cursor, finish):
                    result.append(cursor)
                cursor += timedelta(minutes=cfg['booking']['slot_step_minutes'])
        return result

    def book(self, user_id, name, contact, service_id, staff_id, start, now=None):
        if start not in self.slots(service_id, staff_id, start.astimezone(self.tz).date(), now):
            raise ValueError('Время больше недоступно. Выберите другое время.')
        service = next(s for s in self.config['services'] if s['id'] == service_id)
        staff = next(s for s in self.config['staff'] if s['id'] == staff_id)
        return self.store.reserve(user_id, name, contact, service, staff, start,
                                  self.config['booking']['max_active_per_client'], now)

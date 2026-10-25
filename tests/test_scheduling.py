import tempfile
import unittest
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

from booking_bot.config import load_config
from booking_bot.scheduling import Scheduler
from booking_bot.storage import BookingStore


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = BookingStore(Path(self.temp.name) / 'db.sqlite3')
        self.cfg = load_config()
        self.scheduler = Scheduler(self.cfg, self.store)
        self.now = datetime(2030, 1, 7, 8, tzinfo=self.scheduler.tz)  # Monday
        self.day = self.now.date()

    def slots(self, day=None):
        return self.scheduler.slots('haircut', 'anna', day or self.day, self.now)

    def test_notice_break_and_closing_duration(self):
        times = [s.strftime('%H:%M') for s in self.slots()]
        self.assertNotIn('09:00', times)
        self.assertIn('09:30', times)
        self.assertNotIn('12:30', times)
        self.assertNotIn('13:00', times)
        self.assertIn('14:00', times)
        self.assertIn('17:00', times)
        self.assertNotIn('17:30', times)

    def test_sunday_and_horizon(self):
        self.assertEqual(self.slots(self.day + timedelta(days=6)), [])
        self.assertEqual(self.slots(self.day + timedelta(days=14)), [])
        self.assertEqual(self.slots(self.day - timedelta(days=1)), [])

    def test_staff_service_assignment(self):
        self.assertEqual(self.scheduler.slots('consultation', 'alex', self.day, self.now), [])

    def test_closures_and_reopening(self):
        self.store.set_closed(str(self.day))
        self.assertEqual(self.slots(), [])
        self.store.set_closed(str(self.day), False)
        self.assertTrue(self.slots())
        self.cfg['booking']['closed_dates'] = [str(self.day)]
        self.assertEqual(self.slots(), [])

    def test_individual_staff_schedule_and_holiday(self):
        self.cfg['staff'][0]['schedule'] = {0: [['15:00', '16:00']]}
        self.assertEqual([s.strftime('%H:%M') for s in self.slots()], ['15:00'])
        self.cfg['staff'][0]['closed_dates'] = [str(self.day)]
        self.assertEqual(self.slots(), [])

    def test_confirmation_rechecks_availability(self):
        slot = self.slots()[0]
        self.scheduler.book(1, 'Иван', '1234567', 'haircut', 'anna', slot, self.now)
        self.assertNotIn(slot, self.slots())
        with self.assertRaises(ValueError):
            self.scheduler.book(2, 'Пётр', '1234567', 'haircut', 'anna', slot, self.now)
        self.assertIn(slot, self.scheduler.slots('haircut', 'alex', self.day, self.now))

    def test_closed_after_selection_rejected(self):
        slot = self.slots()[0]
        self.store.set_closed(str(self.day))
        with self.assertRaises(ValueError):
            self.scheduler.book(1, 'Иван', '1234567', 'haircut', 'anna', slot, self.now)

    def test_dst_gap_does_not_offer_nonexistent_time(self):
        from zoneinfo import ZoneInfo
        self.cfg['business']['timezone'] = 'Europe/Berlin'
        self.cfg['schedule'] = {6: [['01:00', '05:00']]}
        self.cfg['booking']['min_notice_minutes'] = 0
        scheduler = Scheduler(self.cfg, self.store)
        slots = scheduler.slots('haircut', 'anna', date(2030, 3, 31), datetime(2030, 3, 30, tzinfo=ZoneInfo('Europe/Berlin')))
        self.assertFalse(any(slot.hour == 2 for slot in slots))

    def test_invalid_yaml_and_settings_fail_early(self):
        path = Path(self.temp.name) / 'bad.yaml'
        path.write_text('business: [')
        with self.assertRaises(ValueError):
            load_config(path)
        import yaml
        self.cfg['booking']['slot_step_minutes'] = 0
        path.write_text(yaml.safe_dump(self.cfg, allow_unicode=True))
        with self.assertRaises(ValueError):
            load_config(path)

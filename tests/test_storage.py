import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

from booking_bot.storage import BookingStore


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'db.sqlite3'
        self.store = BookingStore(self.path)
        self.now = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
        self.slot = self.now + timedelta(minutes=45)
        self.service = dict(id='cut', name='Стрижка', price=100, duration_minutes=60)
        self.staff = dict(id='anna', name='Анна')

    def reserve(self, user=1, slot=None, staff=None):
        return self.store.reserve(user, 'Имя', '+996555123456', self.service, staff or self.staff, slot or self.slot, now=self.now)

    def test_reserve_persists_and_keeps_details(self):
        key = self.reserve()
        row = BookingStore(self.path).get(key)
        self.assertEqual(row['contact'], '+996555123456')
        self.assertEqual(row['service_name'], 'Стрижка')
        self.assertEqual(row['start'], int(self.slot.timestamp()))

    def test_overlap_rejected_in_another_timezone(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.reserve(2, (self.slot + timedelta(minutes=30)).astimezone(timezone(timedelta(hours=6))))

    def test_adjacent_appointments_allowed(self):
        self.reserve()
        self.reserve(2, self.slot + timedelta(hours=1))
        self.assertEqual(len(self.store.upcoming(now=self.now)), 2)

    def test_different_staff_allowed_but_client_cannot_overlap(self):
        self.reserve()
        other = dict(id='alex', name='Алексей')
        self.reserve(2, staff=other)
        with self.assertRaises(ValueError):
            self.reserve(1, staff=dict(id='third', name='Третий'))

    def test_atomic_conflict_with_two_connections(self):
        barrier = Barrier(2)
        def attempt(user):
            barrier.wait()
            try:
                return self.reserve(user)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(attempt, [1, 2]))
        self.assertEqual(sum(key is not None for key in result), 1)

    def test_past_rejected(self):
        with self.assertRaises(ValueError):
            self.reserve(slot=self.now)

    def test_cancel_owner_notice_admin_and_slot_release(self):
        key = self.reserve()
        with self.assertRaises(ValueError):
            self.store.cancel(2, key, now=self.now)
        with self.assertRaises(ValueError):
            self.store.cancel(1, key, notice_minutes=60, now=self.now)
        self.store.cancel(999, key, admin=True, now=self.now)
        self.reserve(2)
        self.assertEqual(self.store.upcoming(1, self.now), [])
        self.assertEqual(self.store.get(key)['status'], 'cancelled')

    def test_active_limit(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.store.reserve(1, 'Имя', '1234567', self.service, self.staff, self.slot + timedelta(hours=2), max_active=1, now=self.now)

    def test_reminders_window_delivery_and_cancellation(self):
        key = self.reserve()
        self.reserve(2, self.slot + timedelta(hours=2))
        self.assertEqual([r['id'] for r in self.store.due(now=self.now)], [key])
        self.store.mark_reminded(key)
        self.assertEqual(self.store.due(now=self.now), [])
        self.assertEqual(len(self.store.due(now=self.now + timedelta(hours=2))), 1)
        self.assertEqual(self.store.due(now=self.now + timedelta(hours=4)), [])

    def test_cancelled_not_reminded(self):
        key = self.reserve()
        self.store.cancel(1, key, now=self.now)
        self.assertEqual(self.store.due(now=self.now), [])

    def test_legacy_import_once_without_deleting_original(self):
        path = Path(self.temp.name) / 'legacy.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE bookings (id INTEGER PRIMARY KEY, user_id INTEGER, slot TEXT, reminded INTEGER)')
            db.execute('INSERT INTO bookings VALUES (1, 42, ?, 0)', (self.slot.isoformat(),))
        migrated = BookingStore(path)
        self.assertEqual(len(BookingStore(path).upcoming(now=self.now)), 1)
        self.assertTrue(migrated.busy('anna', self.slot, self.slot + timedelta(minutes=30)))
        with sqlite3.connect(path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bookings').fetchone()[0], 1)

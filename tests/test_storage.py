import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from booking_bot.storage import BookingStore


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = BookingStore(Path(self.temp.name) / 'db.sqlite3')
        self.now = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
        self.slot = self.now + timedelta(minutes=45)

    def test_reserve_persists(self):
        self.store.reserve(1, self.slot, self.now)
        reopened = BookingStore(self.store.path)
        self.assertEqual(reopened.upcoming(1, self.now)[0][1], self.slot.isoformat())

    def test_same_instant_is_exclusive_across_timezones(self):
        self.store.reserve(1, self.slot, self.now)
        with self.assertRaises(ValueError):
            self.store.reserve(2, self.slot.astimezone(timezone(timedelta(hours=6))), self.now)

    def test_past_rejected(self):
        with self.assertRaises(ValueError):
            self.store.reserve(1, self.now, self.now)

    def test_only_owner_can_cancel_and_slot_is_released(self):
        self.store.reserve(1, self.slot, self.now)
        key = self.store.upcoming(1, self.now)[0][0]
        self.assertFalse(self.store.cancel(2, key))
        self.assertTrue(self.store.cancel(1, key))
        self.store.reserve(2, self.slot, self.now)
        self.assertEqual(self.store.upcoming(1, self.now), [])

    def test_reminders_window_and_delivery_state(self):
        self.store.reserve(1, self.slot, self.now)
        self.store.reserve(2, self.now + timedelta(hours=2), self.now)
        rows = self.store.due(self.now)
        self.assertEqual(len(rows), 1)
        self.store.mark_reminded(rows[0][0])
        self.assertEqual(self.store.due(self.now), [])
        self.assertEqual(len(self.store.due(self.now + timedelta(hours=1))), 1)

    def test_expired_not_listed_or_reminded(self):
        self.store.reserve(1, self.slot, self.now)
        later = self.now + timedelta(hours=1)
        self.assertEqual(self.store.upcoming(1, later), [])
        self.assertEqual(self.store.due(later), [])


if __name__ == '__main__':
    unittest.main()

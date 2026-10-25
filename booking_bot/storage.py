import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


class BookingStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
                slot TEXT NOT NULL UNIQUE, reminded INTEGER NOT NULL DEFAULT 0)''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def reserve(self, user_id, slot, now=None):
        now = now or datetime.now(timezone.utc)
        if slot.tzinfo is None or slot <= now:
            raise ValueError('Выберите время в будущем.')
        key = slot.astimezone(timezone.utc).isoformat()
        try:
            with self.connect() as db:
                db.execute('INSERT INTO bookings(user_id, slot) VALUES (?, ?)', (user_id, key))
        except sqlite3.IntegrityError:
            raise ValueError('Это время уже занято. Выберите другое.') from None

    def upcoming(self, user_id, now=None):
        key = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.connect() as db:
            return db.execute('SELECT id, slot FROM bookings WHERE user_id=? AND slot>? ORDER BY slot', (user_id, key)).fetchall()

    def cancel(self, user_id, booking_id):
        with self.connect() as db:
            return db.execute('DELETE FROM bookings WHERE id=? AND user_id=?', (booking_id, user_id)).rowcount == 1

    def due(self, now=None):
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self.connect() as db:
            return db.execute('SELECT id, user_id, slot FROM bookings WHERE reminded=0 AND slot>? AND slot<=?',
                              (now.isoformat(), (now + timedelta(hours=1)).isoformat())).fetchall()

    def mark_reminded(self, booking_id):
        with self.connect() as db:
            db.execute('UPDATE bookings SET reminded=1 WHERE id=?', (booking_id,))

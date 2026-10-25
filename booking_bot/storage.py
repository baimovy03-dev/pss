import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc


class BookingStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS appointments (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, client_name TEXT NOT NULL,
                    contact TEXT NOT NULL, service_id TEXT NOT NULL, service_name TEXT NOT NULL,
                    staff_id TEXT NOT NULL, staff_name TEXT NOT NULL, price TEXT NOT NULL,
                    start INTEGER NOT NULL, end INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', reminded INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS appointments_slots ON appointments(staff_id, start, end);
                CREATE TABLE IF NOT EXISTS closures (day TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
            ''')
            # Preserve and import the first demo's records once. They block all staff.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bookings'").fetchone() and not db.execute("SELECT 1 FROM migrations WHERE name='legacy'").fetchone():
                for row in db.execute('SELECT * FROM bookings').fetchall():
                    start = int(datetime.fromisoformat(row['slot']).timestamp())
                    db.execute('''INSERT INTO appointments(user_id,client_name,contact,service_id,service_name,
                        staff_id,staff_name,price,start,end,reminded) VALUES (?,?,'','legacy','Запись из демо','*','Любой сотрудник','—',?,?,?)''',
                        (row['user_id'], 'Клиент', start, start + 3600, row['reminded']))
                db.execute("INSERT INTO migrations VALUES ('legacy')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def busy(self, staff_id, start, end):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM appointments WHERE status='active' AND (staff_id=? OR staff_id='*') AND start<? AND end>?",
                                   (staff_id, int(end.timestamp()), int(start.timestamp()))).fetchone())

    def reserve(self, user_id, name, contact, service, staff, start, max_active=3, now=None):
        now = now or datetime.now(UTC)
        if start.tzinfo is None or start <= now:
            raise ValueError('Выберите время в будущем.')
        a = int(start.timestamp())
        b = a + service['duration_minutes'] * 60
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT count(*) FROM appointments WHERE user_id=? AND status='active' AND start>?", (user_id, int(now.timestamp()))).fetchone()[0] >= max_active:
                raise ValueError('Достигнут лимит активных записей. Отмените ненужную запись.')
            if db.execute("SELECT 1 FROM appointments WHERE status='active' AND start<? AND end>? AND (staff_id=? OR staff_id='*' OR user_id=?)",
                          (b, a, staff['id'], user_id)).fetchone():
                raise ValueError('Время уже занято либо пересекается с вашей другой записью.')
            return db.execute('''INSERT INTO appointments(user_id,client_name,contact,service_id,service_name,
                staff_id,staff_name,price,start,end) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (user_id, name, contact, service['id'], service['name'], staff['id'], staff['name'], str(service['price']), a, b)).lastrowid

    def upcoming(self, user_id=None, now=None):
        now = now or datetime.now(UTC)
        query = "SELECT * FROM appointments WHERE status='active' AND start>?"
        args = [int(now.timestamp())]
        if user_id is not None:
            query += ' AND user_id=?'
            args.append(user_id)
        with self.connect() as db:
            return db.execute(query + ' ORDER BY start', args).fetchall()

    def get(self, key):
        with self.connect() as db:
            return db.execute('SELECT * FROM appointments WHERE id=?', (key,)).fetchone()

    def cancel(self, user_id, key, notice_minutes=0, admin=False, now=None):
        now = now or datetime.now(UTC)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM appointments WHERE id=?', (key,)).fetchone()
            if not row or row['status'] != 'active' or (not admin and row['user_id'] != user_id):
                raise ValueError('Запись не найдена.')
            if not admin and row['start'] <= (now + timedelta(minutes=notice_minutes)).timestamp():
                raise ValueError('Для поздней отмены свяжитесь с администратором.')
            db.execute("UPDATE appointments SET status='cancelled' WHERE id=?", (key,))
            return row

    def due(self, minutes=60, now=None):
        now = now or datetime.now(UTC)
        with self.connect() as db:
            return db.execute("SELECT * FROM appointments WHERE status='active' AND reminded=0 AND start>? AND start<=?",
                              (int(now.timestamp()), int((now + timedelta(minutes=minutes)).timestamp()))).fetchall()

    def mark_reminded(self, key):
        with self.connect() as db:
            db.execute('UPDATE appointments SET reminded=1 WHERE id=?', (key,))

    def closed_dates(self):
        with self.connect() as db:
            return {row[0] for row in db.execute('SELECT day FROM closures')}

    def set_closed(self, day, closed=True):
        with self.connect() as db:
            if closed:
                db.execute('INSERT OR IGNORE INTO closures VALUES (?)', (day,))
            else:
                db.execute('DELETE FROM closures WHERE day=?', (day,))

"""Persistent counters and panel settings. No client private keys are stored."""
import sqlite3
import time
import json
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


class Accounting:
    def __init__(self, path, timezone='Europe/Moscow', clock=time.time):
        self.path, self.zone, self.clock = str(path), ZoneInfo(timezone), clock
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS clients (
                    key TEXT PRIMARY KEY, alias TEXT NOT NULL DEFAULT '',
                    quota INTEGER NOT NULL DEFAULT 0,
                    rx INTEGER, tx INTEGER, enforced INTEGER NOT NULL DEFAULT 0,
                    started REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS usage (
                    key TEXT NOT NULL, month TEXT NOT NULL,
                    received INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key, month));
                CREATE TABLE IF NOT EXISTS daily_usage (
                    key TEXT NOT NULL, day TEXT NOT NULL,
                    received INTEGER NOT NULL DEFAULT 0, sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (key, day));
                CREATE INDEX IF NOT EXISTS daily_day ON daily_usage(day);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, timestamp REAL NOT NULL, type TEXT NOT NULL,
                    client_id TEXT NOT NULL, name TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            # Additive, idempotent migration; never rebuild clients or monthly usage.
            columns = {r['name'] for r in db.execute('PRAGMA table_info(clients)')}
            for name, definition in [('handshake', 'REAL NOT NULL DEFAULT 0'),
                                     ('enforced_month', "TEXT NOT NULL DEFAULT ''"),
                                     ('quota_logged', 'INTEGER NOT NULL DEFAULT 0')]:
                if name not in columns:
                    db.execute(f'ALTER TABLE clients ADD COLUMN {name} {definition}')
            if 'enforced_month' not in columns:
                db.execute("""UPDATE clients SET enforced_month=COALESCE(
                    (SELECT MAX(month) FROM usage WHERE usage.key=clients.key), ?)
                    WHERE enforced=1""", (self.month(),))
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)', ('daily_started', str(self.clock())))
            db.execute('PRAGMA user_version=2')

    def connect(self):
        # Connections live for one operation, so polling and HTTP threads can share this object.
        from contextlib import contextmanager
        @contextmanager
        def connection():
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()
        return connection()

    def month(self):
        return datetime.fromtimestamp(self.clock(), self.zone).strftime('%Y-%m')

    def sample(self, keys, live):
        now = self.clock()
        day = datetime.fromtimestamp(now, self.zone).date().isoformat()
        month = day[:7]
        with self.connect() as db:
            for key in keys:
                row = db.execute('SELECT * FROM clients WHERE key=?', (key,)).fetchone()
                current = live.get(key)
                rx = max(0, int(current.get('received', 0))) if current is not None else None
                tx = max(0, int(current.get('sent', 0))) if current is not None else None
                if row is None:
                    # Start observation now; do not call pre-installation traffic this month's usage.
                    db.execute('INSERT INTO clients(key,rx,tx,started) VALUES(?,?,?,?)',
                               (key, rx, tx, self.clock()))
                    dr = dt = 0
                else:
                    # Missing snapshots are not evidence of reset. Preserve the baseline
                    # until counters return; known removals explicitly clear it below.
                    dr = 0 if rx is None else (rx-row['rx'] if row['rx'] is not None and rx >= row['rx'] else rx)
                    dt = 0 if tx is None else (tx-row['tx'] if row['tx'] is not None and tx >= row['tx'] else tx)
                    if current is not None:
                        db.execute('UPDATE clients SET rx=?,tx=? WHERE key=?', (rx, tx, key))
                if current and current.get('handshake', 0):
                    db.execute('UPDATE clients SET handshake=? WHERE key=?', (current['handshake'], key))
                db.execute("""INSERT INTO usage(key,month,received,sent) VALUES(?,?,?,?)
                    ON CONFLICT(key,month) DO UPDATE SET
                    received=received+excluded.received, sent=sent+excluded.sent""",
                           (key, month, dr, dt))
                db.execute("""INSERT INTO daily_usage VALUES(?,?,?,?)
                    ON CONFLICT(key,day) DO UPDATE SET
                    received=received+excluded.received, sent=sent+excluded.sent""", (key, day, dr, dt))

    def view(self):
        with self.connect() as db:
            rows = db.execute("""SELECT c.*, COALESCE(u.received,0) AS received,
                COALESCE(u.sent,0) AS sent FROM clients c LEFT JOIN usage u
                ON c.key=u.key AND u.month=?""", (self.month(),)).fetchall()
        return {r['key']: dict(r, used=r['received']+r['sent'],
                              limited=bool(r['quota'] and r['received']+r['sent'] >= r['quota']))
                for r in rows}

    def edit(self, key, name, quota, display_name=None):
        with self.connect() as db:
            old = db.execute('SELECT alias,quota FROM clients WHERE key=?', (key,)).fetchone()
            db.execute('UPDATE clients SET alias=?,quota=? WHERE key=?', (name, quota, key))
            if old and display_name is not None:
                if old['alias'] != name:
                    self._event(db, key, 'rename', display_name, {'before': old['alias'], 'after': name})
                if old['quota'] != quota:
                    self._event(db, key, 'quota_change', display_name, {'before': old['quota'], 'after': quota})

    def enforcement(self, key, enabled):
        with self.connect() as db:
            if enabled:
                db.execute('UPDATE clients SET enforced=1,enforced_month=? WHERE key=? AND enforced=0', (self.month(), key))
            else:
                db.execute('UPDATE clients SET enforced=0,quota_logged=0 WHERE key=?', (key,))

    def _event(self, db, key, kind, name, details):
        db.execute('INSERT INTO events(timestamp,type,client_id,name,details) VALUES(?,?,?,?,?)',
                   (self.clock(), kind, hashlib.sha256(key.encode()).hexdigest()[:24], name, json.dumps(details)))
        db.execute('DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)')

    def event(self, key, kind, name):
        if kind not in {'manual_block', 'manual_unblock', 'quota_block', 'month_restore', 'quota_restore'}:
            raise ValueError('Unknown event')
        with self.connect() as db:
            row = db.execute('SELECT quota_logged FROM clients WHERE key=?', (key,)).fetchone()
            if kind == 'quota_block':
                if row['quota_logged']:
                    return
                db.execute('UPDATE clients SET quota_logged=1 WHERE key=?', (key,))
            elif kind in {'month_restore', 'quota_restore'}:
                db.execute('UPDATE clients SET enforced=0,quota_logged=0 WHERE key=?', (key,))
            self._event(db, key, kind, name, {})

    def events(self):
        with self.connect() as db:
            return [dict(time=r['timestamp'], type=r['type'], id=r['client_id'], name=r['name'],
                         details=json.loads(r['details']))
                    for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 20')]

    def history(self, key=None, period='7d'):
        if period not in {'7d', 'month'}:
            raise ValueError('Invalid period')
        today = datetime.fromtimestamp(self.clock(), self.zone).date()
        week = today - timedelta(days=6)
        month = today.replace(day=1)
        start = week if period == '7d' else month
        earliest = min(week, month, today-timedelta(days=1))
        clause, args = (' AND key=?', (key,)) if key is not None else ('', ())
        with self.connect() as db:
            rows = db.execute('SELECT day,SUM(received) rx,SUM(sent) tx FROM daily_usage WHERE day>=? AND day<=?'
                              + clause + ' GROUP BY day', (earliest.isoformat(), today.isoformat(), *args)).fetchall()
            monthly = db.execute('SELECT COALESCE(SUM(received),0),COALESCE(SUM(sent),0) FROM usage WHERE month=?'
                                 + clause, (today.strftime('%Y-%m'), *args)).fetchone()
            began = float(db.execute("SELECT value FROM metadata WHERE key='daily_started'").fetchone()[0])
        by_day = {r['day']: (r['rx'], r['tx']) for r in rows}
        def total(rx, tx):
            return dict(rx=rx, tx=tx, total=rx+tx)
        def summed(first, last):
            values = [v for d, v in by_day.items() if first.isoformat() <= d <= last.isoformat()]
            return total(sum(v[0] for v in values), sum(v[1] for v in values))
        days = []
        for offset in range((today-start).days+1):
            day = start + timedelta(days=offset)
            days.append(dict(day=day.isoformat(), **total(*by_day.get(day.isoformat(), (0, 0)))))
        known_month = summed(month, today)
        return dict(days=days, period=period, timezone=str(self.zone), history_started=began,
                    summary=dict(today=summed(today, today), yesterday=summed(today-timedelta(days=1), today-timedelta(days=1)),
                                 week=summed(week, today), month=total(*monthly)),
                    unallocated_month=total(max(0, monthly[0]-known_month['rx']), max(0, monthly[1]-known_month['tx'])))

    def removed(self, key):
        with self.connect() as db:
            db.execute('UPDATE clients SET rx=NULL,tx=NULL WHERE key=?', (key,))

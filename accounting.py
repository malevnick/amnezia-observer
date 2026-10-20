"""Persistent counters and panel settings. No client private keys are stored."""
import sqlite3
import time
from datetime import datetime
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
            """)

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
        month = self.month()
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
                    # A counter dropping or a peer disappearing is a new counter generation.
                    dr = 0 if rx is None else (rx-row['rx'] if row['rx'] is not None and rx >= row['rx'] else rx)
                    dt = 0 if tx is None else (tx-row['tx'] if row['tx'] is not None and tx >= row['tx'] else tx)
                    db.execute('UPDATE clients SET rx=?,tx=? WHERE key=?', (rx, tx, key))
                db.execute("""INSERT INTO usage(key,month,received,sent) VALUES(?,?,?,?)
                    ON CONFLICT(key,month) DO UPDATE SET
                    received=received+excluded.received, sent=sent+excluded.sent""",
                           (key, month, dr, dt))

    def view(self):
        with self.connect() as db:
            rows = db.execute("""SELECT c.*, COALESCE(u.received,0) AS received,
                COALESCE(u.sent,0) AS sent FROM clients c LEFT JOIN usage u
                ON c.key=u.key AND u.month=?""", (self.month(),)).fetchall()
        return {r['key']: dict(r, used=r['received']+r['sent'],
                              limited=bool(r['quota'] and r['received']+r['sent'] >= r['quota']))
                for r in rows}

    def edit(self, key, name, quota):
        with self.connect() as db:
            db.execute('UPDATE clients SET alias=?,quota=? WHERE key=?', (name, quota, key))

    def enforcement(self, key, enabled):
        with self.connect() as db:
            db.execute('UPDATE clients SET enforced=? WHERE key=?', (int(enabled), key))

    def removed(self, key):
        with self.connect() as db:
            db.execute('UPDATE clients SET rx=NULL,tx=NULL WHERE key=?', (key,))

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
from accounting import Accounting
from backend import Monitor, BackendError
from test_app import KEY, OTHER, Driver
import test_v3


class DailyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'old.sqlite3'
        self.now = datetime(2026,10,31,23,59,50,tzinfo=ZoneInfo('Europe/Moscow')).timestamp()
        self.a = Accounting(self.path, clock=lambda:self.now)

    def tearDown(self): self.tmp.cleanup()

    def sample(self, rx, tx): self.a.sample([KEY], {KEY:dict(received=rx,sent=tx)})

    def test_migration_preserves_old_data_and_does_not_invent_days(self):
        self.path.unlink()
        with sqlite3.connect(self.path) as db:
            db.executescript("""CREATE TABLE clients(key TEXT PRIMARY KEY,alias TEXT NOT NULL DEFAULT '',
                quota INTEGER NOT NULL DEFAULT 0,rx INTEGER,tx INTEGER,enforced INTEGER NOT NULL DEFAULT 0,started REAL NOT NULL);
                CREATE TABLE usage(key TEXT,month TEXT,received INTEGER,sent INTEGER,PRIMARY KEY(key,month));""")
            db.execute('INSERT INTO clients VALUES(?,?,?,?,?,?,?)',(KEY,'Phone',9000,100,200,1,123))
            db.execute('INSERT INTO usage VALUES(?,?,?,?)',(KEY,'2026-10',50,70))
        db.close()
        self.a = Accounting(self.path,clock=lambda:self.now)
        self.sample(110,220)
        row=self.a.view()[KEY]
        self.assertEqual((row['alias'],row['quota'],row['started'],row['enforced']),('Phone',9000,123,1))
        self.assertEqual(row['used'],150)
        h=self.a.history(KEY)
        self.assertEqual(h['summary']['today'],dict(rx=10,tx=20,total=30))
        self.assertEqual(h['unallocated_month']['total'],120)
        self.assertEqual(sum(d['total'] for d in h['days']),30)
        again=Accounting(self.path,clock=lambda:self.now)
        self.assertEqual(again.history(KEY),h)

    def test_midnight_month_boundary_and_restart(self):
        self.sample(100,200); self.sample(110,220)
        self.now += 20
        self.sample(115,227)
        h=self.a.history(KEY,'month')
        self.assertEqual(len(h['days']),1)
        self.assertEqual(h['summary']['today']['total'],12)
        self.assertEqual(h['summary']['yesterday']['total'],30)
        self.assertEqual(h['summary']['week']['total'],42)
        self.assertEqual(h['summary']['month']['total'],12)
        self.a=Accounting(self.path,clock=lambda:self.now)
        self.sample(115,227)
        self.assertEqual(self.a.history(KEY)['summary']['week']['total'],42)

    def test_missing_peer_preserved_and_reset_counted_once(self):
        self.sample(100,200);self.sample(150,260)
        self.a.sample([KEY],{})
        self.sample(150,260)
        self.assertEqual(self.a.view()[KEY]['used'],110)
        self.a.sample([], {})  # disappears from config, too
        self.sample(153,264)
        self.assertEqual(self.a.view()[KEY]['used'],117)
        self.sample(3,4)  # independent counter reset
        self.sample(3,4)
        self.assertEqual(self.a.history(KEY)['summary']['today']['total'],124)
        self.a.removed(KEY)  # confirmed removal starts a new generation
        self.sample(5,6)
        self.assertEqual(self.a.view()[KEY]['used'],135)

    def test_empty_period_and_seven_calendar_days(self):
        self.assertEqual(len(self.a.history()['days']),7)
        self.assertEqual(len(self.a.history(period='month')['days']),31)
        self.sample(0,0);self.sample(10,20)
        self.now+=6*86400
        self.assertEqual(self.a.history()['summary']['week']['total'],30)
        self.now+=86400
        self.assertEqual(self.a.history()['summary']['week']['total'],0)

    def test_retention_and_safe_event_identifiers(self):
        self.sample(0,0)
        for i in range(1005): self.a.event(KEY,'manual_block','Phone')
        with self.a.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM events').fetchone()[0],1000)
            self.assertNotIn(KEY,str([tuple(r) for r in db.execute('SELECT * FROM events')]))
        self.assertEqual(len(self.a.events()),20)
        self.assertEqual(self.a.events()[0]['id'],Monitor.identifier(KEY))


class MonitorHistoryTests(unittest.TestCase):
    setUp=test_v3.QuotaTests.setUp
    tearDown=test_v3.QuotaTests.tearDown
    consume=test_v3.QuotaTests.consume
    peer=test_v3.QuotaTests.peer

    def test_events_survive_restart_and_include_settings(self):
        self.monitor.edit(self.id,'Phone',500)
        self.monitor.action(self.id,True)
        self.monitor.action(self.id,False)
        newer=Monitor(self.driver,self.path);newer.metrics=lambda:{};newer.collect()
        events=newer.view()['actions']
        self.assertEqual([e['type'] for e in events],['manual_unblock','manual_block','quota_change','rename'])
        self.assertEqual(events[0]['name'],'Phone')
        self.assertNotIn(KEY,json.dumps(events))

    def test_confirmed_quota_and_month_events_only_once(self):
        self.monitor.edit(self.id,'Phone',100);self.consume(100,0)
        self.monitor.collect();self.monitor.collect()
        self.assertEqual([e['type'] for e in self.monitor.view()['actions']].count('quota_block'),1)
        self.now+=20
        with patch.object(self.driver,'restore',side_effect=BackendError('offline')):self.monitor.collect()
        self.assertNotIn('month_restore',[e['type'] for e in self.monitor.view()['actions']])
        self.monitor.collect();self.monitor.collect()
        self.assertEqual([e['type'] for e in self.monitor.view()['actions']].count('month_restore'),1)

    def test_details_safe_and_separate_from_status(self):
        self.driver.peers[KEY]['presharedkey']='SECRET'
        self.driver.live[KEY]['handshake']=123
        self.consume(25,35)
        detail=self.monitor.history(self.id)
        self.assertEqual(detail['peer']['amnezia_name'],"Nick's phone")
        self.assertEqual(detail['summary']['today']['total'],60)
        self.assertNotIn('days',self.monitor.view())
        self.assertNotIn(KEY,json.dumps(detail));self.assertNotIn('SECRET',json.dumps(detail))
        self.monitor.action(self.id,True)
        self.assertEqual(self.monitor.history(self.id)['peer']['handshake'],123)
        with self.assertRaises(BackendError):self.monitor.history('0'*24)


class HistoryWebTests(unittest.TestCase):
    setUpClass=classmethod(test_v3.PublicWebTests.setUpClass.__func__)
    setUp=test_v3.PublicWebTests.setUp
    login=test_v3.PublicWebTests.login
    post=test_v3.PublicWebTests.post

    def test_protected_routes_periods_and_light_dashboard(self):
        self.assertEqual(self.c.get('/amnadmin/api/history').status_code,401)
        self.assertNotIn('history-chart',self.c.get('/amnadmin/history').text)
        self.login()
        for url in ['/amnadmin/history','/amnadmin/clients/'+'1'*24]:
            page=self.c.get(url)
            self.assertEqual(page.status_code,200)
            self.assertIn('history-chart',page.text)
        self.assertNotIn('history.js',self.c.get('/amnadmin/').text)
        self.assertEqual(self.c.get('/amnadmin/api/history?period=month').status_code,200)
        self.assertEqual(self.c.get('/amnadmin/api/history?period=year').status_code,400)
        self.assertEqual(self.c.get('/amnadmin/api/history?id=bad').status_code,400)
        self.assertEqual(self.c.get('/amnadmin/api/history?id='+'0'*24).status_code,409)
        self.assertEqual(self.c.post('/amnadmin/api/edit',json=dict(id='1'*24,name='x',quota=0)).status_code,403)

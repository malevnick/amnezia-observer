import base64
from datetime import datetime
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app import create_app, Demo
from backend import Monitor, BackendError
from test_app import Driver, KEY, OTHER
from werkzeug.security import generate_password_hash


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'blocked.json'
        self.driver = Driver()
        self.monitor = Monitor(self.driver, self.path)
        self.monitor.metrics = lambda: {}
        self.now = datetime(2026, 10, 31, 23, 59, 50, tzinfo=ZoneInfo('Europe/Moscow')).timestamp()
        self.monitor.accounting.clock = lambda: self.now
        self.monitor.collect()
        self.id = self.monitor.identifier(KEY)

    def tearDown(self):
        self.tmp.cleanup()

    def peer(self):
        return next(p for p in self.monitor.view()['peers'] if p['id'] == self.id)

    def consume(self, rx, tx):
        self.driver.live[KEY]['received'] += rx
        self.driver.live[KEY]['sent'] += tx
        self.monitor.collect()
        self.assertIsNone(self.monitor.error)

    def test_baseline_and_persistence_and_counter_reset(self):
        self.assertEqual(self.peer()['month_used'], 0)
        self.consume(30, 70)
        newer = Monitor(self.driver, self.path)
        newer.metrics = lambda: {}
        newer.accounting.clock = lambda: self.now
        self.monitor = newer
        self.monitor.collect()
        self.assertEqual(self.peer()['month_used'], 100)
        self.driver.live[KEY].update(received=4, sent=5)
        self.monitor.collect()
        self.assertEqual(self.peer()['month_used'], 109)
        del self.driver.live[KEY]
        self.monitor.collect()
        self.driver.live[KEY] = dict(received=10, sent=20, handshake=0)
        self.monitor.collect()
        self.assertEqual(self.peer()['month_used'], 139)

    def test_limit_is_upload_plus_download_and_unblock_guard(self):
        self.monitor.edit(self.id, 'Телефон', 100)
        self.consume(40, 60)
        self.assertNotIn(KEY, self.driver.live)
        self.assertIn(OTHER, self.driver.live)
        self.assertTrue(self.peer()['quota_blocked'])
        with self.assertRaises(BackendError):
            self.monitor.action(self.id, False)
        self.monitor.edit(self.id, 'Телефон', 200)
        self.assertIn(KEY, self.driver.live)
        self.assertFalse(self.peer()['blocked'])
        self.assertEqual(self.peer()['month_used'], 100)

    def test_month_rollover_restores_only_quota_block(self):
        self.monitor.edit(self.id, '', 100)
        self.consume(100, 0)
        self.monitor.action(self.monitor.identifier(OTHER), True)
        self.now += 20
        self.monitor.collect()
        self.assertIn(KEY, self.driver.live)
        self.assertNotIn(OTHER, self.driver.live)
        self.assertEqual(self.peer()['month_used'], 0)
        self.assertEqual(self.peer()['month'], '2026-11')

    def test_manual_and_quota_blocks_can_coexist(self):
        self.monitor.edit(self.id, '', 100)
        self.consume(100, 0)
        self.monitor.action(self.id, True)
        self.now += 20
        self.monitor.collect()
        self.assertNotIn(KEY, self.driver.live)
        self.assertTrue(self.peer()['manual_blocked'])
        self.monitor.action(self.id, False)
        self.assertIn(KEY, self.driver.live)

    def test_month_rollover_restoration_retries_after_failure(self):
        self.monitor.edit(self.id, '', 100)
        self.consume(100, 0)
        self.now += 20
        with patch.object(self.driver, 'restore', side_effect=BackendError('Unavailable')):
            self.monitor.collect()
        self.assertEqual(self.monitor.error, 'Unavailable')
        self.monitor.collect()
        self.assertIn(KEY, self.driver.live)
        self.assertIsNone(self.monitor.error)

    def test_month_rollover_never_resurrects_revoked_peer(self):
        self.monitor.edit(self.id, '', 100)
        self.consume(100, 0)
        del self.driver.peers[KEY]
        self.now += 20
        self.monitor.collect()
        self.assertNotIn(KEY, self.driver.live)

    def test_manual_block_preserves_counters(self):
        self.consume(50, 50)
        self.monitor.action(self.id, True)
        self.monitor.action(self.id, False)
        self.driver.live[KEY].update(received=10, sent=20)
        self.monitor.collect()
        self.assertEqual(self.peer()['month_used'], 130)

    def test_alias_survives_restart_without_modifying_vpn(self):
        self.monitor.edit(self.id, '<script>my phone</script>', 500)
        newer = Monitor(self.driver, self.path)
        newer.metrics = lambda: {}
        newer.accounting.clock = lambda: self.now
        newer.collect()
        self.assertEqual(newer.view()['peers'][0]['name'], '<script>my phone</script>')
        self.assertEqual(self.driver.snapshot()[1][KEY], "Nick's phone")

    def test_settings_validation(self):
        for name, quota in [('test', True), ('a'*81, 0), ('x\ncommand', 0), ('x', -1)]:
            with self.assertRaises(BackendError):
                self.monitor.edit(self.id, name, quota)

    def test_legacy_blocks_migrate_without_discarding(self):
        self.path.write_text(json.dumps([KEY]))
        newer = Monitor(self.driver, self.path)
        newer.metrics = lambda: {}
        newer.collect()
        self.assertNotIn(KEY, self.driver.live)
        self.assertTrue(newer.view()['peers'][0]['manual_blocked'])


class PublicWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = generate_password_hash('test-password')

    def setUp(self):
        self.app = create_app(Demo(), dict(TESTING=True, URL_PREFIX='/amnadmin',
                                          SECRET_KEY='x'*32, ADMIN_PASSWORD_HASH=self.pw,
                                          SESSION_COOKIE_SECURE=True))
        self.c = self.app.test_client()
        self.origin = 'https://localhost:8443'
        response = self.c.get('/amnadmin/', base_url=self.origin)
        self.initial_cookie = response.headers['Set-Cookie']
        self.csrf = re.search('name="csrf-token" content="([^"]+)"', response.text)[1]

    def post(self, path, data):
        return self.c.post('/amnadmin'+path, base_url=self.origin, json=data,
                           headers={'X-CSRF-Token': self.csrf})

    def login(self):
        self.assertEqual(self.post('/login', dict(username='admin', password='test-password')).status_code, 200)
        response = self.c.get('/amnadmin/', base_url=self.origin)
        self.csrf = re.search('name="csrf-token" content="([^"]+)"', response.text)[1]
        return response

    def test_prefix_login_static_status_and_secure_cookie(self):
        self.assertEqual(self.c.get('/').status_code, 404)
        r = self.c.get('/amnadmin/', base_url=self.origin)
        self.assertIn('/amnadmin/static/ui.js', r.text)
        self.assertIn('content="/amnadmin/"', r.text)
        self.assertIn('Secure', self.initial_cookie)
        self.assertIn('Path=/amnadmin/', self.initial_cookie)
        self.login()
        self.assertEqual(self.c.get('/amnadmin/api/status', base_url=self.origin).status_code, 200)
        response = self.c.get('/amnadmin/static/ui.js', base_url=self.origin)
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_rename_and_quota_api(self):
        self.login()
        r = self.post('/api/edit', dict(id='1'*24, name='New name', quota=1024**3))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['peers'][0]['name'], 'New name')
        self.assertEqual(self.post('/api/edit', dict(id='1'*24, name='x', quota=True)).status_code, 400)

    def test_qr_requires_auth_and_csrf(self):
        self.assertEqual(self.post('/api/qr', dict(text='vpn://abcd')).status_code, 401)
        self.login()
        self.assertEqual(self.c.post('/amnadmin/api/qr', json={'text':'vpn://abcd'}, base_url=self.origin).status_code, 403)

    def test_qr_native_config_and_key(self):
        self.login()
        config = f'[Interface]\nPrivateKey={KEY}\nAddress=10.8.1.2/32\n[Peer]\nPublicKey={OTHER}\nEndpoint=192.0.2.1:39138\nAllowedIPs=0.0.0.0/0\n'
        for text in (config, 'vpn://test-key-123'):
            r = self.post('/api/qr', dict(text=text))
            self.assertEqual(r.status_code, 200)
            image = base64.b64decode(r.json['image'].partition(',')[2])
            self.assertIn(b'<svg', image)
            self.assertNotIn(KEY.encode(), image)
            self.assertEqual(r.headers['Cache-Control'], 'no-store')
        for text in (config.replace('[Peer]', 'PostUp=echo bad\n[Peer]'), 'garbage', 'x'*2201):
            self.assertEqual(self.post('/api/qr', dict(text=text)).status_code, 400)

    def test_bad_json_types(self):
        self.login()
        for endpoint in ('/api/action', '/api/edit', '/api/qr'):
            self.assertEqual(self.post(endpoint, ['bad']).status_code, 400)


class HttpsSetupTests(unittest.TestCase):
    def test_existing_credentials_preserved_and_prefix_configured(self):
        from https_setup import setup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'web.env').write_text('SECRET_KEY=keep\nADMIN_PASSWORD_HASH=hash\nCOOKIE_SECURE=0\nPANEL_HOST=localhost\n')
            (root/'server.crt').write_text('test certificate')
            (root/'server.key').write_text('test key')
            with patch('https_setup.subprocess.run') as run:
                run.return_value.returncode = 0
                setup('192.0.2.1', 8443, root)
            env = (root/'web.env').read_text()
            self.assertIn('ADMIN_PASSWORD_HASH=hash', env)
            self.assertIn('COOKIE_SECURE=1', env)
            self.assertIn('URL_PREFIX=/amnadmin', env)
            self.assertEqual(env.count('PANEL_HOST='), 1)
            config = (root/'nginx.conf').read_text()
            self.assertIn('listen 8443 ssl;', config)
            self.assertIn('gzip on;', config)
            self.assertIn('ssl_buffer_size 4k;', config)
            api_config = config.split('location /amnadmin/api/ {', 1)[1].split('}', 1)[0]
            self.assertIn('gzip off;', api_config)
            self.assertIn('proxy_buffering on;', api_config)
            self.assertIn('proxy_buffer_size 16k;', api_config)
            self.assertIn('proxy_pass http://127.0.0.1:5055;', config)
            for name in ('client_body', 'proxy', 'fastcgi', 'uwsgi', 'scgi'):
                self.assertIn(name+'_temp_path /run/amnezia-observer-https/', config)
            self.assertNotIn('listen 80', config)
            self.assertFalse(any('req' in call.args[0] for call in run.call_args_list))
            with patch('https_setup.subprocess.run') as run:
                run.return_value.returncode = 0
                setup('192.0.2.1', 443, root)
            config = (root/'nginx.conf').read_text()
            self.assertIn('listen 443 ssl;', config)
            self.assertNotIn('listen 8443 ssl;', config)

    def test_invalid_ip_and_internal_port_rejected(self):
        from https_setup import setup
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for ip, port in [('192.0.2.1; malicious', 8443), ('192.0.2.1', 5055), ('192.0.2.1', 70000)]:
                with self.assertRaises(ValueError):
                    setup(ip, port, root)
            self.assertEqual(list(root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import generate_password_hash
from app import create_app, Demo
from backend import Monitor, BackendError, parse_peers, DockerDriver

KEY = base64.b64encode(b'a'*32).decode()
OTHER = base64.b64encode(b'b'*32).decode()


class Driver:
    def __init__(self):
        self.peers = {k:dict(publickey=k, allowedips=f'10.8.1.{i+2}/32') for i,k in enumerate([KEY,OTHER])}
        self.live = {k:dict(handshake=0, received=1, sent=2) for k in self.peers}
        self.restored = None
    def snapshot(self): return self.peers.copy(), {KEY:"Nick's phone"}, self.live.copy()
    def remove(self,k): self.live.pop(k,None)
    def restore(self,k,p): self.restored=p.copy(); self.live[k]={}


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'state.json'
        self.d=Driver()
        self.m=Monitor(self.d,self.path)
        self.m.metrics=lambda:{}
    def tearDown(self): self.tmp.cleanup()
    def test_block_only_selected_and_restart_reconcile(self):
        self.m.action(self.m.identifier(KEY),True)
        self.assertNotIn(KEY,self.d.live)
        self.assertIn(OTHER,self.d.live)
        self.assertEqual(json.loads(self.path.read_text()),[KEY])
        self.d.live[KEY]={}  # Amnezia restart or app reapplies config
        newer=Monitor(self.d,self.path);newer.metrics=lambda:{};newer.collect()
        self.assertNotIn(KEY,self.d.live)
        self.assertEqual(newer.view()['peers'][0]['state'],'blocked')
    def test_restore_current_config_not_old(self):
        self.m.action(self.m.identifier(KEY),True)
        self.d.peers[KEY]['allowedips']='10.8.1.99/32'
        self.m.action(self.m.identifier(KEY),False)
        self.assertEqual(self.d.restored['allowedips'],'10.8.1.99/32')
        self.assertEqual(json.loads(self.path.read_text()),[])
    def test_deleted_peer_cannot_be_resurrected(self):
        self.m.action(self.m.identifier(KEY),True);del self.d.peers[KEY]
        with self.assertRaises(BackendError):self.m.action(self.m.identifier(KEY),False)
        self.assertNotIn(KEY,self.d.live)
    def test_failure_is_not_offline(self):
        self.m.collect()
        with patch.object(self.d,'snapshot',side_effect=BackendError('Docker unavailable')):self.m.collect()
        self.assertEqual(self.m.view()['error'],'Docker unavailable')
        self.assertEqual(len(self.m.view()['peers']),2)
    def test_save_failure_unblock_reblocks(self):
        self.m.action(self.m.identifier(KEY),True)
        with patch.object(self.m,'save',side_effect=OSError()):
            with self.assertRaises(BackendError):self.m.action(self.m.identifier(KEY),False)
        self.assertNotIn(KEY,self.d.live)
        self.assertIn(KEY,self.m.blocked)
    def test_secret_not_exposed(self):
        self.d.peers[KEY]['presharedkey']='SECRET';self.m.collect()
        self.assertNotIn('SECRET',json.dumps(self.m.view()))
        self.assertNotIn(KEY,json.dumps(self.m.view()))
    def test_invalid_state_fails_closed(self):
        self.path.write_text('{broken')
        with self.assertRaises(ValueError): Monitor(self.d,self.path)
    def test_config_parser_separates_interface_and_peers(self):
        p=parse_peers(f'[Interface]\nPrivateKey=secret\n[Peer]\nPublicKey={KEY}\nAllowedIPs=10.8.1.2/32\n')
        self.assertNotIn('privatekey',p[KEY])
    def test_commands_no_shell_and_psk_stdin(self):
        d=DockerDriver()
        with patch.object(d,'command') as command:
            d.restore(KEY,dict(allowedips='10.8.1.2/32',presharedkey=OTHER))
            args,stdin=command.call_args.args
            self.assertNotIn(OTHER,args);self.assertEqual(stdin,OTHER+'\n')


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.pw=generate_password_hash('test-password')
    def setUp(self):
        self.demo=Demo()
        self.app=create_app(self.demo,dict(TESTING=True,URL_PREFIX='',SECRET_KEY='x'*32,ADMIN_PASSWORD_HASH=self.pw))
        self.c=self.app.test_client();self.c.get('/')
    def csrf(self):
        with self.c.session_transaction() as s:return s['csrf']
    def login(self):
        return self.c.post('/login',json=dict(username='admin',password='test-password'),headers={'X-CSRF-Token':self.csrf()})
    def test_auth_csrf_and_action(self):
        self.assertEqual(self.c.get('/api/status').status_code,401)
        self.assertEqual(self.c.post('/login',json={}).status_code,403)
        self.assertEqual(self.login().status_code,200)
        self.assertEqual(self.c.post('/api/action',json=dict(id='1'*24,block=True)).status_code,403)
        r=self.c.post('/api/action',json=dict(id='1'*24,block=True),headers={'X-CSRF-Token':self.csrf()})
        self.assertEqual(r.status_code,200);self.assertTrue(r.json['peers'][0]['blocked'])
    def test_forged_xff_does_not_bypass_limit(self):
        for i in range(5):
            r=self.c.post('/login',json={'password':'bad'},headers={'X-CSRF-Token':self.csrf(),'X-Forwarded-For':f'1.2.3.{i}'})
            self.assertEqual(r.status_code,401)
        self.assertEqual(self.login().status_code,429)
    def test_security_headers_and_host(self):
        r=self.c.get('/');self.assertIn("script-src 'self'",r.headers['Content-Security-Policy'])
        self.assertEqual(self.c.get('/',headers={'Host':'evil.example'}).status_code,400)
    def test_invalid_action(self):
        self.login()
        self.assertEqual(self.c.post('/api/action',json={'id':'1'*24,'block':'false'},headers={'X-CSRF-Token':self.csrf()}).status_code,400)
    def test_login_never_gets_credentials_in_url(self):
        html=self.c.get('/').text
        self.assertIn('method="post"',html)
        self.assertNotIn('onclick=',html)


if __name__=='__main__':unittest.main()

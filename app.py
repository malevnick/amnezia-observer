import hmac
import os
import secrets
import threading
import time
import base64
import io
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session
from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.exceptions import NotFound
from werkzeug.security import check_password_hash, generate_password_hash
from backend import BackendError, BrokerClient


class Demo:
    def __init__(self):
        self.blocked = set()
        self.actions = []
        self.lock = threading.RLock()
        self.settings = {}

    def view(self):
        with self.lock:
            peers = []
            for i, name in enumerate(['iPhone • личный', 'MacBook Air', 'Планшет']):
                key = str(i+1)*24
                settings = self.settings.get(key, {})
                peers.append(dict(id=key, name=settings.get('name') or name, addresses=f'10.8.1.{i+2}/32',
                    state='blocked' if key in self.blocked else ('online' if i<2 else 'offline'),
                    manual_blocked=key in self.blocked, quota_blocked=False, quota=settings.get('quota', 0),
                    month_used=1024000000*(i+1), month='Демо',
                    blocked=key in self.blocked, handshake=time.time()-(45 if i<2 else 86000),
                    received=134000000*(i+1), sent=890000000*(i+1), endpoint='Скрыт в демо'))
            return dict(demo=True, error=None, collected_at=time.time(), peers=peers, actions=self.actions,
                orphaned_blocks=0, metrics=dict(cpu=3.4, memory_available=510*1024**2,
                memory_total=930*1024**2, disk_free=15*1024**3, disk_total=19*1024**3,
                rx_rate=120000, tx_rate=1280000, interface='eth0', uptime=113432))

    def action(self, peer_id, block):
        with self.lock:
            peer = next((p for p in self.view()['peers'] if p['id']==peer_id), None)
            if not peer:
                raise BackendError('Клиент не найден')
            self.blocked.add(peer_id) if block else self.blocked.discard(peer_id)
            self.actions.insert(0, dict(time=time.time(), name=peer['name'], block=block))
            self.actions = self.actions[:20]
            return self.view()

    def edit(self, peer_id, name, quota):
        with self.lock:
            if not any(p['id'] == peer_id for p in self.view()['peers']):
                raise BackendError('Клиент не найден')
            self.settings[peer_id] = dict(name=name, quota=quota)
            return self.view()


def create_app(backend=None, config=None):
    app = Flask(__name__)
    demo = os.getenv('DEMO', '0') == '1'
    secret = os.getenv('SECRET_KEY', '')
    password_hash = os.getenv('ADMIN_PASSWORD_HASH', '')
    if demo:
        secret = secret or secrets.token_hex(32)
        password_hash = password_hash or generate_password_hash('demo-preview')
    app.config.update(SECRET_KEY=secret, ADMIN_PASSWORD_HASH=password_hash,
        ADMIN_USER=os.getenv('ADMIN_USER', 'admin'), DEMO=demo,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', '0')=='1',
        URL_PREFIX=os.getenv('URL_PREFIX', '/amnadmin'),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2), MAX_CONTENT_LENGTH=16384)
    if config:
        app.config.update(config)
    if len(app.config['SECRET_KEY'])<32 or not app.config['ADMIN_PASSWORD_HASH']:
        raise RuntimeError('Set a random SECRET_KEY and ADMIN_PASSWORD_HASH; no default production credentials')
    prefix = app.config['URL_PREFIX']
    if prefix not in {'', '/amnadmin'}:
        raise RuntimeError('URL_PREFIX must be empty or /amnadmin')
    app.config['SESSION_COOKIE_PATH'] = (prefix or '') + '/'
    app.config['SESSION_COOKIE_NAME'] = 'amnadmin_session'
    service = backend or (Demo() if demo else BrokerClient())
    failures = {}
    failure_lock = threading.Lock()

    @app.before_request
    def protect():
        if request.host.split(':')[0] not in {'localhost', '127.0.0.1', os.getenv('PANEL_HOST', 'localhost')}:
            return jsonify(error='Недопустимый Host'), 400
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        if request.method == 'POST':
            if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), session['csrf']):
                return jsonify(error='Сессия устарела. Обновите страницу.'), 403
        if request.path.startswith('/api/') and not session.get('logged_in'):
            return jsonify(error='Войдите в панель'), 401

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store' if not request.path.startswith('/static/') else 'public, max-age=3600'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/')
    def index():
        return render_template('index.html' if session.get('logged_in') else 'login.html',
                               csrf=session['csrf'], demo=app.config['DEMO'])

    @app.post('/login')
    def login():
        # Never trust X-Forwarded-For from an unauthenticated request.
        ip, now = request.remote_addr, time.time()
        with failure_lock:
            for key in list(failures):
                failures[key] = [t for t in failures[key] if now-t<300]
                if not failures[key]:
                    failures.pop(key)
            if len(failures.get(ip, []))>=5:
                return jsonify(error='Слишком много попыток. Подождите 5 минут.'), 429
            failures.setdefault(ip, []).append(now)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify(error='Некорректный запрос'), 400
        password = data.get('password', '')
        if not isinstance(password, str) or len(password)>256:
            return jsonify(error='Неверный логин или пароль'), 401
        good = check_password_hash(app.config['ADMIN_PASSWORD_HASH'], password)
        if not good or data.get('username')!=app.config['ADMIN_USER']:
            return jsonify(error='Неверный логин или пароль'), 401
        with failure_lock:
            failures.pop(ip, None)
        session.clear()
        session.update(logged_in=True, csrf=secrets.token_urlsafe(32))
        session.permanent = True
        return jsonify(ok=True)

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/status')
    def status():
        try:
            return jsonify(service.view())
        except BackendError as e:
            return jsonify(error=str(e)), 503

    @app.post('/api/action')
    def action():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or not isinstance(data.get('id'), str) or len(data['id'])!=24 or type(data.get('block')) is not bool:
            return jsonify(error='Некорректный запрос'), 400
        try:
            return jsonify(service.action(data['id'], data['block']))
        except BackendError as e:
            return jsonify(error=str(e)), 409

    @app.post('/api/edit')
    def edit():
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or not isinstance(data.get('id'), str) or len(data['id']) != 24
                or not isinstance(data.get('name'), str) or len(data['name']) > 80
                or any(ord(c) < 32 or ord(c) == 127 for c in data['name'])
                or type(data.get('quota')) is not int or not 0 <= data['quota'] <= 1024**5):
            return jsonify(error='Проверьте имя и лимит'), 400
        try:
            return jsonify(service.edit(data['id'], data['name'], data['quota']))
        except BackendError as e:
            return jsonify(error=str(e)), 409

    @app.post('/api/qr')
    def qr():
        # Transient conversion only. No filesystem, logs, broker, external QR services.
        import qrcode
        import qrcode.image.svg
        from qrcode.exceptions import DataOverflowError
        from configparser import ConfigParser, Error as ConfigError
        from backend import valid_key
        data = request.get_json(silent=True)
        text = data.get('text') if isinstance(data, dict) else None
        if not isinstance(text, str) or not 1 <= len(text.encode()) <= 2200:
            return jsonify(error='Нужен клиентский конфиг или vpn:// ключ, до 2200 байт'), 400
        text = text.strip()
        if text.startswith('vpn://'):
            import re
            if not re.fullmatch(r'vpn://[A-Za-z0-9_+/=-]+', text):
                return jsonify(error='Некорректный vpn:// ключ'), 400
        else:
            parser = ConfigParser(interpolation=None)
            try:
                parser.read_string(text)
                if (set(parser.sections()) != {'Interface', 'Peer'}
                        or not valid_key(parser['Interface'].get('PrivateKey', ''))
                        or not valid_key(parser['Peer'].get('PublicKey', ''))
                        or not parser['Peer'].get('Endpoint')
                        or not parser['Interface'].get('Address')):
                    raise ValueError()
                # Never encode executable wg-quick hooks from an uploaded profile.
                if any(key.lower() in {'preup', 'postup', 'predown', 'postdown'}
                       for section in parser.values() for key in section):
                    raise ValueError()
            except (ConfigError, ValueError):
                return jsonify(error='Нужен клиентский .conf с одним Peer, без команд PreUp/PostUp'), 400
        try:
            code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=5, border=4)
            code.add_data(text)
            code.make(fit=True)
            output = io.BytesIO()
            code.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(output)
        except DataOverflowError:
            return jsonify(error='Профиль слишком большой для одного QR. Используйте импорт файла.'), 400
        return jsonify(image='data:image/svg+xml;base64,' + base64.b64encode(output.getvalue()).decode())

    if prefix:
        app.wsgi_app = DispatcherMiddleware(NotFound(), {prefix: app.wsgi_app})
    return app


if __name__ == '__main__':
    from waitress import serve
    serve(create_app(), host='127.0.0.1', port=int(os.getenv('PORT', '5055')), threads=4)

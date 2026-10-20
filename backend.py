"""Privileged local collector. No shell execution, no HTTP listener on the network."""
import base64
import hashlib
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from accounting import Accounting


class BackendError(Exception):
    pass


def valid_key(value):
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, TypeError):
        return False


def parse_peers(config):
    peers, current = {}, None
    for raw in config.splitlines():
        line = raw.split('#', 1)[0].strip()
        if line.startswith('['):
            current = {} if line.lower() == '[peer]' else None
        elif current is not None and '=' in line:
            key, value = (s.strip() for s in line.split('=', 1))
            current[key.lower()] = value
            if 'publickey' in current:
                peers[current['publickey']] = current
    if any(not valid_key(k) for k in peers):
        raise BackendError('Некорректный ключ в конфигурации VPN')
    return peers


class DockerDriver:
    def __init__(self):
        self.container = os.getenv('AWG_CONTAINER', 'amnezia-awg2')
        self.interface = os.getenv('AWG_INTERFACE', 'awg0')
        self.directory = os.getenv('AWG_DIRECTORY', '/opt/amnezia/awg')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]+', self.container + self.interface):
            raise ValueError('Invalid container/interface')

    def command(self, args, stdin=None):
        try:
            p = subprocess.run(['docker', 'exec', '-i', self.container, *args],
                               input=stdin, text=True, capture_output=True, timeout=6)
        except (OSError, subprocess.TimeoutExpired):
            raise BackendError('Нет ответа от Docker/Amnezia') from None
        if p.returncode:
            # stderr and dump can contain credentials: never expose them.
            raise BackendError('Команда Amnezia не выполнена; проверьте контейнер и интерфейс')
        return p.stdout

    def snapshot(self):
        peers = parse_peers(self.command(['cat', self.directory + '/' + self.interface + '.conf']))
        names = {}
        try:
            table = json.loads(self.command(['cat', self.directory + '/clientsTable']))
            for entry in table:
                names[entry['clientId']] = entry.get('userData', {}).get('clientName', '')
        except (BackendError, ValueError, KeyError, TypeError):
            pass  # Names are optional; VPN errors are not.
        live = {}
        dump = self.command(['awg', 'show', self.interface, 'dump'])
        for line in dump.splitlines()[1:]:
            fields = line.split('\t')
            if len(fields) < 8:
                raise BackendError('Неожиданный формат статистики Amnezia')
            live[fields[0]] = dict(handshake=int(fields[4]), received=int(fields[5]),
                                   sent=int(fields[6]), endpoint=fields[2])
        return peers, names, live

    def remove(self, key):
        if not valid_key(key):
            raise BackendError('Некорректный ключ')
        self.command(['awg', 'set', self.interface, 'peer', key, 'remove'])

    def restore(self, key, peer):
        if not valid_key(key):
            raise BackendError('Некорректный ключ')
        ips = peer.get('allowedips', '')
        try:
            if not ips:
                raise ValueError()
            for address in ips.split(','):
                ipaddress.ip_network(address.strip(), strict=False)
        except ValueError:
            raise BackendError('Некорректный AllowedIPs; доступ не изменён') from None
        args = ['awg', 'set', self.interface, 'peer', key, 'allowed-ips', ips]
        psk = peer.get('presharedkey')
        if psk:
            if not valid_key(psk):
                raise BackendError('Некорректный PresharedKey')
            args += ['preshared-key', '/dev/stdin']
        keepalive = peer.get('persistentkeepalive', '0')
        if not keepalive.isdigit() or not 0 <= int(keepalive) <= 65535:
            raise BackendError('Некорректный keepalive')
        args += ['persistent-keepalive', keepalive]
        if peer.get('endpoint'):
            args += ['endpoint', peer['endpoint']]
        self.command(args, psk + '\n' if psk else None)


class Monitor:
    def __init__(self, driver, state_path):
        self.driver, self.path = driver, Path(state_path)
        self.lock = threading.RLock()
        self.blocked = set()
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if not isinstance(data, list) or any(not valid_key(k) for k in data):
                raise ValueError('Invalid saved block list; refusing to discard it')
            self.blocked = set(data)
        self.latest = None
        self.error = None
        self.last_cpu = self.last_net = None
        self.actions = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.accounting = Accounting(self.path.with_suffix('.sqlite3'), os.getenv('MONTH_TIMEZONE', 'Europe/Moscow'))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(sorted(self.blocked), f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)

    @staticmethod
    def identifier(key):
        return hashlib.sha256(key.encode()).hexdigest()[:24]

    def metrics(self):
        now = time.monotonic()
        values = [int(v) for v in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
        cpu = (sum(values), values[3] + values[4])
        percent = None
        if self.last_cpu and cpu[0] > self.last_cpu[0]:
            percent = round(100 * (1 - (cpu[1]-self.last_cpu[1])/(cpu[0]-self.last_cpu[0])), 1)
        self.last_cpu = cpu
        mem = {line.split(':')[0]: int(line.split()[1])*1024
               for line in Path('/proc/meminfo').read_text().splitlines()}
        # Only the default-route NIC, never sum Docker bridges + tunnel + NIC.
        iface = os.getenv('NETWORK_INTERFACE', '')
        if not iface:
            for line in Path('/proc/net/route').read_text().splitlines()[1:]:
                parts = line.split()
                if parts[1] == '00000000':
                    iface = parts[0]
                    break
        net = None
        for line in Path('/proc/net/dev').read_text().splitlines()[2:]:
            name, vals = line.split(':', 1)
            if name.strip() == iface:
                fields = vals.split()
                net = (int(fields[0]), int(fields[8]), now)
        rates = [None, None]
        if net and self.last_net and now > self.last_net[2]:
            rates = [max(0, (net[i]-self.last_net[i])/(now-self.last_net[2])) for i in (0, 1)]
        self.last_net = net
        disk = shutil.disk_usage('/')
        return dict(cpu=percent, memory_total=mem['MemTotal'], memory_available=mem['MemAvailable'],
                    disk_total=disk.total, disk_free=disk.free, rx_rate=rates[0], tx_rate=rates[1],
                    interface=iface, uptime=int(float(Path('/proc/uptime').read_text().split()[0])))

    def collect(self):
        with self.lock:
            try:
                config, names, live = self.driver.snapshot()
                self.accounting.sample(config, live)
                usage = self.accounting.view()
                # Record quota enforcement before changing the interface. A failed restore
                # is retried next poll, including after a restart/month rollover.
                for key in config:
                    row = usage[key]
                    if row['limited']:
                        self.accounting.enforcement(key, True)
                    elif row['enforced'] and key not in self.blocked:
                        if key not in live:
                            self.driver.restore(key, config[key])
                        self.accounting.enforcement(key, False)
                quota_blocks = {k for k in config if usage[k]['limited']}
                effective = self.blocked | quota_blocks
                # A revoked peer is never restored from an old saved config.
                for key in effective & live.keys():
                    self.driver.remove(key)
                    self.accounting.removed(key)
                config, names, live = self.driver.snapshot()
                peers = []
                for key, peer in config.items():
                    status = live.get(key, {})
                    blocked = key in effective
                    row = usage.get(key)
                    if row is None:
                        raise BackendError('Список клиентов изменился; ожидается следующий опрос')
                    peers.append(dict(id=self.identifier(key), name=row['alias'] or names.get(key) or 'Устройство ' + key[:6],
                        addresses=peer.get('allowedips', ''), blocked=blocked,
                        manual_blocked=key in self.blocked, quota_blocked=row['limited'],
                        quota=row['quota'], month_used=row['used'], month=self.accounting.month(),
                        accounting_started=row['started'],
                        state=('blocked' if key not in live else 'pending') if blocked else
                              ('online' if time.time()-status.get('handshake', 0)<180 else 'offline'),
                        handshake=status.get('handshake'), received=row['received'],
                        sent=row['sent'], endpoint=status.get('endpoint')))
                self.latest = dict(peers=peers, metrics=self.metrics(), collected_at=time.time(),
                                   timezone=str(self.accounting.zone), orphaned_blocks=len(self.blocked-config.keys()))
                self.error = None
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, BackendError) else 'Ошибка сборщика статистики'

    def view(self):
        with self.lock:
            return dict(self.latest or {}, error=self.error, actions=list(self.actions), demo=False)

    def action(self, peer_id, block):
        with self.lock:
            peers, names, live = self.driver.snapshot()
            key = next((k for k in peers if self.identifier(k) == peer_id), None)
            if key is None:
                raise BackendError('Клиент удалён из Amnezia или больше не существует')
            self.accounting.sample(peers, live)
            if not block and self.accounting.view()[key]['limited']:
                raise BackendError('Месячный лимит исчерпан. Сначала увеличьте или отключите лимит.')
            if block:
                old = set(self.blocked)
                self.blocked.add(key)
                try:
                    self.save()  # Persist intent before touching live interface.
                except Exception:
                    self.blocked = old
                    raise BackendError('Не удалось сохранить блокировку; доступ не изменён') from None
                self.driver.remove(key)
                self.accounting.removed(key)
            else:
                # If persistence fails, reconcile will block again: fail closed.
                self.driver.restore(key, peers[key])
                old = set(self.blocked)
                self.blocked.discard(key)
                try:
                    self.save()
                except Exception:
                    self.blocked = old
                    self.driver.remove(key)
                    raise BackendError('Не удалось сохранить разблокировку') from None
            _, _, actual = self.driver.snapshot()
            if (key in actual) == block:
                raise BackendError('Изменение не подтверждено Amnezia; проверьте состояние')
            self.actions.insert(0, dict(time=time.time(), name=names.get(key) or key[:6], block=block))
            self.actions = self.actions[:20]
            self.collect()
            return self.view()

    def edit(self, peer_id, name, quota):
        if (not isinstance(name, str) or len(name) > 80 or
                any(ord(c) < 32 or ord(c) == 127 for c in name) or
                type(quota) is not int or not 0 <= quota <= 1024**5):
            raise BackendError('Имя: до 80 символов; лимит: неотрицательное число байтов')
        with self.lock:
            peers, _, live = self.driver.snapshot()
            key = next((k for k in peers if self.identifier(k) == peer_id), None)
            if key is None:
                raise BackendError('Клиент больше не существует')
            self.accounting.sample(peers, live)
            self.accounting.edit(key, name.strip(), quota)
            self.collect()
            if self.error:
                raise BackendError('Настройки сохранены, но применение не завершено: ' + self.error)
            return self.view()


class LocalConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.host)


class BrokerClient:
    def request(self, method, path, data=None):
        conn = LocalConnection(os.getenv('BROKER_SOCKET', '/run/amnezia-observer/broker.sock'), timeout=25)
        try:
            conn.request(method, path, json.dumps(data) if data else None)
            r = conn.getresponse()
            payload = json.loads(r.read())
            if r.status != 200:
                raise BackendError(payload.get('error', 'Ошибка локальной службы'))
            return payload
        except (OSError, ValueError, http.client.HTTPException):
            raise BackendError('Локальная служба недоступна') from None
        finally:
            conn.close()

    def view(self):
        return self.request('GET', '/status')

    def action(self, peer_id, block):
        return self.request('POST', '/action', dict(id=peer_id, block=block))

    def edit(self, peer_id, name, quota):
        return self.request('POST', '/edit', dict(id=peer_id, name=name, quota=quota))


def serve():
    monitor = Monitor(DockerDriver(), os.getenv('STATE_FILE', '/var/lib/amnezia-observer/blocked.json'))
    def poll():
        while True:
            monitor.collect()
            time.sleep(3)
    threading.Thread(target=poll, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(200, monitor.view()) if self.path == '/status' else self.reply(404, {})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', 0))
                if self.path not in {'/action', '/edit'} or not 0 < size < 4096:
                    return self.reply(400, {})
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict) or not isinstance(data.get('id'), str) or not re.fullmatch('[a-f0-9]{24}', data['id']):
                    return self.reply(400, {})
                if self.path == '/edit':
                    self.reply(200, monitor.edit(data['id'], data.get('name'), data.get('quota')))
                elif type(data.get('block')) is bool:
                    self.reply(200, monitor.action(data['id'], data['block']))
                else:
                    self.reply(400, {})
            except BackendError as e:
                self.reply(409, dict(error=str(e)))
            except Exception:
                self.reply(500, dict(error='Ошибка локальной службы'))

        def reply(self, code, data):
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
    path = Path(os.getenv('BROKER_SOCKET', '/run/amnezia-observer/broker.sock'))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o660)
        server.serve_forever()


if __name__ == '__main__':
    serve()

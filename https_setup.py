"""Configure a dedicated nginx instance; do not change system nginx sites."""
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess


def setup(ip, port, root=Path('/etc/amnezia-observer')):
    ip = str(ipaddress.IPv4Address(ip))
    if (port != 443 and not 1024 <= port <= 65535) or port == 5055:
        raise ValueError('Choose 443 or a port from 1024 to 65535, except internal port 5055')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert, key = root / 'server.crt', root / 'server.key'
    valid = cert.exists() and key.exists()
    if valid:
        valid = all(subprocess.run(command, capture_output=True).returncode == 0 for command in (
            ['openssl', 'verify', '-CAfile', str(cert), '-verify_ip', ip, str(cert)],
            ['openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '2592000']))
    if not valid:
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '365', '-keyout', str(key), '-out', str(cert),
                        '-subj', '/CN=' + ip, '-addext', 'subjectAltName=IP:' + ip],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        key.chmod(0o600)
        cert.chmod(0o644)
    env = root / 'web.env'
    lines = env.read_text().splitlines()
    replaced = {'COOKIE_SECURE', 'PANEL_HOST', 'URL_PREFIX'}
    lines = [line for line in lines if line.partition('=')[0] not in replaced]
    lines += ['COOKIE_SECURE=1', 'PANEL_HOST=' + ip, 'URL_PREFIX=/amnadmin']
    temporary = env.with_suffix('.tmp')
    temporary.write_text('\n'.join(lines) + '\n')
    temporary.chmod(0o600)
    os.replace(temporary, env)
    (root / 'nginx.conf').write_text(f"""
user amnezia-observer;
worker_processes 1;
pid /run/amnezia-observer-https/nginx.pid;
error_log stderr warn;
events {{ worker_connections 128; }}
http {{
    access_log off;
    server_tokens off;
    gzip on;
    gzip_vary on;
    gzip_min_length 256;
    gzip_comp_level 4;
    gzip_types text/css text/javascript application/javascript application/json;
    ssl_buffer_size 4k;
    client_max_body_size 16k;
    client_body_buffer_size 16k;
    client_body_temp_path /run/amnezia-observer-https/body;
    proxy_temp_path /run/amnezia-observer-https/proxy;
    fastcgi_temp_path /run/amnezia-observer-https/fastcgi;
    uwsgi_temp_path /run/amnezia-observer-https/uwsgi;
    scgi_temp_path /run/amnezia-observer-https/scgi;
    client_header_timeout 10s;
    client_body_timeout 15s;
    send_timeout 30s;
    keepalive_timeout 20s;
    server {{
        listen {port} ssl;
        server_name {ip};
        ssl_certificate {cert};
        ssl_certificate_key {key};
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:observer_tls:1m;
        location = /amnadmin {{ return 308 /amnadmin/; }}
        location /amnadmin/api/ {{
            gzip off;
            proxy_pass http://127.0.0.1:5055;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_connect_timeout 3s;
            proxy_read_timeout 30s;
            proxy_buffering on;
            proxy_buffer_size 16k;
            proxy_buffers 4 16k;
            proxy_max_temp_file_size 0;
        }}
        location /amnadmin/ {{
            proxy_pass http://127.0.0.1:5055;
            proxy_set_header Host $http_host;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_connect_timeout 3s;
            proxy_read_timeout 30s;
            proxy_request_buffering off;
            proxy_buffering off;
        }}
        location / {{ return 404; }}
    }}
}}
""")
    (root / 'address').write_text(f'https://{ip}:{port}/amnadmin/\n')
    print(f'Panel URL: https://{ip}:{port}/amnadmin/')
    print('Self-signed certificate SHA-256 fingerprint:')
    subprocess.run(['openssl', 'x509', '-in', str(cert), '-noout', '-fingerprint', '-sha256'], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', required=True)
    parser.add_argument('--port', type=int, default=8443)
    args = parser.parse_args()
    setup(args.ip, args.port)

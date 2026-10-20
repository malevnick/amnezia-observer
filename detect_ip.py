"""Find the server's public IPv4 without requiring interactive configuration."""
import ipaddress
import socket
import urllib.request


def public_ipv4(value):
    address = ipaddress.IPv4Address(value.strip())
    if not address.is_global:
        raise ValueError('Not a public IPv4')
    return str(address)


def detect():
    try:
        # UDP connect selects the source address; no application packet is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(('1.1.1.1', 53))
            return public_ipv4(connection.getsockname()[0])
    except (OSError, ValueError):
        pass
    # NAT deployments may not have the public address on any local interface.
    try:
        with urllib.request.urlopen('https://api4.ipify.org', timeout=8) as response:
            return public_ipv4(response.read(64).decode('ascii'))
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError('Cannot detect public IPv4. Run PANEL_IP=YOUR_PUBLIC_IP bash install.sh') from None


if __name__ == '__main__':
    try:
        print(detect())
    except RuntimeError as exc:
        raise SystemExit(str(exc))

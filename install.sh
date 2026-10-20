#!/bin/sh
# Debian installer; run explicitly as root. Does not restart Amnezia.
set -eu
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET=/opt/amnezia-observer
if test -e "$TARGET/app.py" || test -e /etc/amnezia-observer/web.env; then
 echo 'Observer is already installed. This installer is for a fresh installation only.'
 exit 1
fi
command -v docker >/dev/null
docker exec amnezia-awg2 awg show awg0 public-key >/dev/null
docker exec amnezia-awg2 test -r /opt/amnezia/awg/awg0.conf
if test -L "$TARGET"; then echo 'Refusing symlink installation directory'; exit 1; fi
if test -z "${PANEL_IP:-}"; then
 PANEL_IP=$(python3 "$SOURCE/detect_ip.py")
 echo "Detected public IPv4: $PANEL_IP"
fi
PANEL_PORT=${PANEL_PORT:-8443}
# Validate before installing packages or stopping any Observer service.
python3 -c 'import ipaddress,sys; ipaddress.IPv4Address(sys.argv[1]); p=int(sys.argv[2]); assert 1024<=p<=65535 and p!=5055' "$PANEL_IP" "$PANEL_PORT"
apt-get update
apt-get install -y python3-venv openssl tzdata
# Prevent a newly installed distribution nginx from opening the default port 80.
# An existing nginx installation and its services/sites are left alone.
MASKED=0
cleanup_mask() {
 if test "$MASKED" = 1; then systemctl unmask --runtime nginx.service; fi
}
trap cleanup_mask EXIT
if ! command -v nginx >/dev/null 2>&1; then
 if ! test -e /etc/systemd/system/nginx.service && ! test -L /etc/systemd/system/nginx.service && ! test -e /run/systemd/system/nginx.service && ! test -L /run/systemd/system/nginx.service; then
  systemctl mask --runtime nginx.service
  MASKED=1
 fi
 apt-get install -y nginx-light
 if test "$MASKED" = 1; then systemctl disable nginx.service; fi
fi
if ! id amnezia-observer >/dev/null 2>&1; then
 useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin amnezia-observer
fi
install -d -m 755 "$TARGET" "$TARGET/static" "$TARGET/templates"
install -d -m 700 /etc/amnezia-observer
if test "$SOURCE" != "$TARGET"; then
 for file in app.py backend.py accounting.py detect_ip.py configure.py https_setup.py recover.py requirements.txt requirements.lock; do
  install -m 644 "$SOURCE/$file" "$TARGET/$file"
 done
 install -m 644 "$SOURCE"/static/* "$TARGET/static/"
 install -m 644 "$SOURCE"/templates/* "$TARGET/templates/"
fi
python3 -m venv "$TARGET/venv"
"$TARGET/venv/bin/python" -m pip install -r "$TARGET/requirements.lock"
if ! test -f /etc/amnezia-observer/web.env; then
 cd /etc/amnezia-observer
 "$TARGET/venv/bin/python" "$TARGET/configure.py"
fi
if ! test -f /etc/amnezia-observer/broker.env; then
 printf '%s\n' 'AWG_CONTAINER=amnezia-awg2' 'AWG_INTERFACE=awg0' 'AWG_DIRECTORY=/opt/amnezia/awg' > /etc/amnezia-observer/broker.env
fi
chmod 600 /etc/amnezia-observer/*.env
"$TARGET/venv/bin/python" "$TARGET/https_setup.py" --ip "$PANEL_IP" --port "$PANEL_PORT"
install -m 644 "$SOURCE"/deploy/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now amnezia-observer-broker.service amnezia-observer-web.service amnezia-observer-https.service
systemctl --no-pager --full status amnezia-observer-broker.service amnezia-observer-web.service amnezia-observer-https.service
cat /etc/amnezia-observer/address
echo 'Login: admin. Use the password you entered during installation.'
echo 'Allow the chosen TCP port in your firewall if needed. The installer does not change firewall rules.'
echo 'Certificate expires in one year; see README for renewal instructions.'

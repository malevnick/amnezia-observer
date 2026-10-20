# Обновление установленного Observer

Новая версия автоматически дополняет SQLite при первом запуске сборщика.
Перед обновлением сохраните код и состояние. На VPS от root, из каталога
клонированного репозитория, в котором нет ваших незакоммиченных правок:

```bash
git pull --ff-only
bash <<'UPDATE'
set -eu
test "$(id -u)" = 0
SOURCE=$(pwd)
test -f "$SOURCE/static/history.js"
BACKUP=/var/backups/amnezia-observer/$(date +%Y%m%d-%H%M%S)
install -d -m 700 "$BACKUP"
systemctl stop amnezia-observer-web amnezia-observer-broker
trap 'systemctl start amnezia-observer-broker amnezia-observer-web amnezia-observer-https' EXIT
cp -a /opt/amnezia-observer "$BACKUP/app"
cp -a /var/lib/amnezia-observer "$BACKUP/state"
cp -a /etc/amnezia-observer "$BACKUP/config"
for file in app.py backend.py accounting.py; do
  install -m 644 "$SOURCE/$file" "/opt/amnezia-observer/$file"
done
install -m 644 "$SOURCE"/static/* /opt/amnezia-observer/static/
install -m 644 "$SOURCE"/templates/* /opt/amnezia-observer/templates/
/opt/amnezia-observer/venv/bin/python -m py_compile /opt/amnezia-observer/app.py /opt/amnezia-observer/backend.py /opt/amnezia-observer/accounting.py
systemctl start amnezia-observer-broker amnezia-observer-web amnezia-observer-https
trap - EXIT
printf 'Backup: %s\n' "$BACKUP"
systemctl --no-pager status amnezia-observer-broker amnezia-observer-web amnezia-observer-https
UPDATE
```

Эти команды предназначены для перехода с предыдущей v3 на версию с историей:
новых Python-пакетов не требуется. Пароль, сертификат, настройки nginx и порт
сохраняются. Amnezia/Docker не перезапускаются. Пока сборщик остановлен, квоты
не проверяются; уже применённые блокировки остаются в VPN-интерфейсе.

После запуска откройте панель и карточку клиента. Если запуск не удался, проверьте
`journalctl -u amnezia-observer-broker -u amnezia-observer-web -n 60 --no-pager`.
Не удаляйте SQLite и не запускайте повторно установщик для чистой установки.
Бэкап содержит секреты панели: не публикуйте его и не переносите в GitHub.
Для отката остановите обе службы, восстановите код и состояние из одной и той же
резервной копии и запустите службы. Трафик и события после момента копии при таком
откате будут потеряны; перед откатом сохраните также текущее состояние.

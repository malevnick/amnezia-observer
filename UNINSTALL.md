# Удаление Amnezia Observer

Эта инструкция удаляет панель. Docker, контейнеры AmneziaWG/DNS и VPN-конфиги
остаются на сервере. Если нужно удалить сам VPN, это отдельное действие в Amnezia.

## 1. Снять блокировки и удалить панель

Войдите на VPS по SSH как root. Блок ниже сначала отключает службы панели,
затем возвращает доступ клиентам, которых блокировал Observer, и только после
успешного восстановления удаляет файлы. Удалённые в самой Amnezia клиенты
не восстанавливаются. Имена, месячная статистика, пароль панели и TLS-ключ
будут удалены без возможности восстановления, если у вас нет резервной копии.

```bash
bash <<'UNINSTALL'
set -eu
test "$(id -u)" = 0 || { echo 'Run as root'; exit 1; }
systemctl disable --now amnezia-observer-https.service amnezia-observer-web.service amnezia-observer-broker.service
cd /opt/amnezia-observer
python3 recover.py --unblock-all
cd /
rm -f -- /etc/systemd/system/amnezia-observer-https.service \
  /etc/systemd/system/amnezia-observer-web.service \
  /etc/systemd/system/amnezia-observer-broker.service
systemctl daemon-reload
rm -rf -- /opt/amnezia-observer /etc/amnezia-observer \
  /var/lib/amnezia-observer /run/amnezia-observer /run/amnezia-observer-https
if id amnezia-observer >/dev/null 2>&1; then userdel amnezia-observer; fi
if getent group amnezia-observer >/dev/null; then groupdel amnezia-observer; fi
echo 'Observer removed. Amnezia VPN and DNS were not removed or restarted.'
UNINSTALL
```

Если восстановление доступа завершилось ошибкой, блок остановится до удаления
файлов. Исправьте доступ к Docker/Amnezia и повторите восстановление. Не удаляйте
состояние блокировок до успешного `recover.py --unblock-all`.
Команды рассчитаны на полную штатную установку. При частично удалённой панели
не игнорируйте ошибки вслепую: проверьте оставшиеся службы и файлы отдельно.

Для нестандартных настроек контейнера перед запуском блока экспортируйте через `export` те же
`AWG_CONTAINER`, `AWG_INTERFACE`, `AWG_DIRECTORY`, что использовал сборщик.

## 2. Удалить оставшиеся копии по желанию

Старая локальная установка могла создавать резервные копии. Если они больше
не нужны, удалить **только бэкапы Observer** можно так:

```bash
rm -rf -- /var/backups/amnezia-observer
```

Удалите также каталог клонированного репозитория и скачанные архивы, если они
остались. Например, если клонировали именно в `/root/amnezia-observer`:

```bash
rm -rf -- /root/amnezia-observer
```

Если файлы лежат в другом месте, сначала проверьте полный путь. Не удаляйте
весь `/root`, `/opt`, каталоги Docker или бэкапы самого VPN. Временные скрипты
и архивы ручного развёртывания не создаются публичным установщиком; удаляйте
их только по известным вам точным именам.

## 3. Firewall и сертификат в браузере

Если вы вручную открывали порт исключительно для панели, удалите соответствующее
правило в firewall VPS и панели хостинга. Например, для ранее добавленного
правила UFW на TCP 8443:

```bash
ufw delete allow 8443/tcp
```

Не удаляйте правила SSH, порта VPN или TCP 443, если его используют другие сайты.
Если вручную добавляли сертификат панели в хранилище доверия компьютера,
удалите именно этот сертификат. Обычное исключение браузера тоже можно удалить.

## 4. Общие системные пакеты

Python venv и все Python-зависимости панели удаляются вместе с `/opt/amnezia-observer`.
Системные `python3-venv`, `openssl`, `tzdata`, `git` могут использоваться другими
программами; инструкция намеренно их не удаляет.

Если nginx был установлен **только для Observer**, других сайтов или служб
nginx нет и он больше не нужен, можно удалить его отдельно:

```bash
apt-get purge nginx-light nginx nginx-common
```

Сначала проверьте список пакетов, который предложит apt. Не запускайте автоматическое
удаление зависимостей вслепую. Не удаляйте Docker/containerd: они нужны Amnezia.
Исторические записи systemd journal могут остаться до обычной ротации; очищать
весь журнал сервера ради записей панели не требуется.

## 5. Проверка

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
ss -lntp
```

Контейнеры Amnezia должны оставаться работающими. Процесс Observer на 127.0.0.1:5055
и выделенный nginx панели должны исчезнуть. Удаление панели не отзывает VPN-профили.

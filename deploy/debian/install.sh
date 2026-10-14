#!/usr/bin/env bash
# Установка AI-приставки на Debian / FreedomBox.
#
# Установка одной командой с сервера каталога (если код уже скопирован):
#   sudo bash install.sh
#
# Скрипт создаёт служебного пользователя, ставит зависимости в venv,
# ставит systemd-юнит и запускает службу. Секреты — в /etc/ai-bridge.env
# (создаётся из примера, правьте вручную — в репозиторий не попадают).

# Защита от CRLF: архив, собранный на Windows, может нести \r\n, а bash
# на Debian читает «pipefail\r» как неизвестную опцию. Если нашли \r —
# перезапускаем себя же, но очищенными переносами.
if grep -q $'\r' "$0" 2>/dev/null; then
    exec bash <(tr -d '\r' < "$0") "$@"
fi

set -euo pipefail

APP_DIR=/opt/ai-bridge
DATA_DIR=/var/lib/ai-bridge
SERVICE_USER=ai-bridge

if [ "$(id -u)" -ne 0 ]; then
    echo "запустите через sudo: sudo bash install.sh" >&2
    exit 1
fi

echo "== 1/6: зависимости системы"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip

echo "== 2/6: служебный пользователя (без home, без шелла)"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "== 3/6: каталоги"
mkdir -p "$APP_DIR" "$DATA_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR"
# venv создаётся от служебного пользователя — каталог приложения должен
# быть ему доступен на запись.
chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "== 4/6: зависимости проекта (venv)"
if [ ! -d "$APP_DIR/.venv" ]; then
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_DIR/.venv"
fi
# --no-cache-dir: у служебного пользователя нет home для кэша pip.
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet --no-cache-dir --upgrade pip
sudo -u "$SERVICE_USER" "$APP_DIR/.venv/bin/pip" install --quiet --no-cache-dir httpx

echo "== 5/6: секреты"
if [ ! -f /etc/ai-bridge.env ]; then
    install -m 600 "$APP_DIR/deploy/debian/ai-bridge.env.example" /etc/ai-bridge.env
    echo "ВНИМАНИЕ: заполните /etc/ai-bridge.env (логин каталога) и запустите снова:"
    echo "    sudo bash $APP_DIR/deploy/debian/install.sh"
    exit 1
fi
chmod 600 /etc/ai-bridge.env

echo "== 6/6: служба systemd"
install -m 644 "$APP_DIR/deploy/debian/ai-bridge.service" /etc/systemd/system/ai-bridge.service
systemctl daemon-reload
systemctl enable --now ai-bridge.service
sleep 2
systemctl --no-pager status ai-bridge.service | head -n 8 || true

echo
echo "Готово. Проверка:  curl -s http://127.0.0.1:8080/v1/models"
echo "Журнал:            journalctl -u ai-bridge -f"
echo "Токен приставки:   sudo -u $SERVICE_USER AI_BRIDGE_HOME=$DATA_DIR $APP_DIR/.venv/bin/python $APP_DIR/app.py --token"

#!/usr/bin/env bash

set -Ee

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log() {
    echo -e "${GREEN}[OK]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

trap 'error "Ошибка в строке $LINENO. Проверь вывод выше."' ERR

clear 2>/dev/null || true

echo -e "${CYAN}"
echo "=========================================="
echo "        SIP BOT LAUNCHER"
echo "          UNIVERSAL LAUNCHER"
echo "=========================================="
echo -e "${NC}"

PROJECT_DIR="$(cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)"
cd "$PROJECT_DIR"

log "Проект: $PROJECT_DIR"

echo
echo "Компьютер: $(hostname)"
echo "Пользователь: $(whoami)"
echo "Система: $(uname -s)"

if ! command -v python3 >/dev/null 2>&1; then
    error "Python3 не установлен"
fi

PYTHON_VERSION="$(python3 --version)"
log "$PYTHON_VERSION найден"

[ -f "$PROJECT_DIR/main.py" ] \
    || error "main.py не найден"

VENV="$HOME/.venv"
VENV_PYTHON="$VENV/bin/python"

if [ ! -x "$VENV_PYTHON" ]; then

    if [ -e "$VENV" ]; then
        error "Найдено поврежденное окружение: $VENV. Автоматическое удаление запрещено."
    fi

    warn "Глобальное окружение не найдено"
    log "Создаем $VENV"

    python3 -m venv "$VENV" \
        || error "Не удалось создать виртуальное окружение"

else
    log "Используем существующее окружение: $VENV"
fi

"$VENV_PYTHON" -m pip --version \
    || error "pip отсутствует в виртуальном окружении"
"$VENV_PYTHON" -m pip install --upgrade pip

REQUIREMENTS="$PROJECT_DIR/requirements.txt"

if [ -f "$REQUIREMENTS" ]; then

    REQUIREMENTS_HASH="$(sha256sum "$REQUIREMENTS" | awk '{print $1}')"
    HASH_FILE="$VENV/.requirements_hash"

    OLD_HASH=""
    if [ -f "$HASH_FILE" ]; then
        OLD_HASH="$(cat "$HASH_FILE")"
    fi

    if [ "$REQUIREMENTS_HASH" != "$OLD_HASH" ]; then

        warn "Список зависимостей изменился или установка выполняется впервые"

        "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS" \
            || error "Не удалось установить зависимости"

        printf '%s\n' "$REQUIREMENTS_HASH" > "$HASH_FILE"

        log "Зависимости установлены"

    else

        log "Зависимости не изменились. Установка пропущена"
    fi

else
    warn "requirements.txt не найден, пропускаем установку зависимых"
fi

# ==========================================
# Создание ярлыков (меню приложений + рабочий стол)
# ==========================================

DESKTOP_MENU_FILE="$HOME/.local/share/applications/SIP_Bot.desktop"
DESKTOP_SHORTCUT="$HOME/Desktop/SIP_Bot.desktop"
ICON_PATH="$PROJECT_DIR/icons/bot.svg"

mkdir -p "$(dirname "$DESKTOP_MENU_FILE")"

if [ ! -f "$DESKTOP_MENU_FILE" ]; then
    cat > "$DESKTOP_MENU_FILE" <<EOF
[Desktop Entry]
Name=SIP Bot
Comment=SIP bot with video support
GenericName=SIP Bot
Exec=$PROJECT_DIR/run.sh
Icon=$ICON_PATH
Type=Application
Categories=Network;Utility;
Keywords=sip;video;bot;
Terminal=false
StartupWMClass=SIP_Bot
EOF
    chmod +x "$DESKTOP_MENU_FILE"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi

log "Ярлык в меню: $DESKTOP_MENU_FILE"

if [ ! -f "$ICON_PATH" ]; then
    warn "Иконка не найдена: $ICON_PATH (ярлык будет без иконки)"
fi

mkdir -p "$HOME/Desktop"

if [ ! -f "$DESKTOP_SHORTCUT" ]; then
    cat > "$DESKTOP_SHORTCUT" <<EOF
[Desktop Entry]
Name=SIP Bot
Comment=SIP bot with video support
Exec=$PROJECT_DIR/run.sh
Icon=$ICON_PATH
Type=Application
Categories=Utility;Network;
Keywords=sip;video;bot;
Terminal=false
StartupWMClass=SIP_Bot
EOF
    chmod +x "$DESKTOP_SHORTCUT"
fi

log "Ярлык на рабочем столе: $DESKTOP_SHORTCUT"

echo -e "${CYAN}"
echo "=========================================="
echo "          STARTING SIP BOT"
echo "=========================================="
echo -e "${NC}"

log "Окружение: $VENV"

exec "$VENV_PYTHON" "$PROJECT_DIR/main.py"

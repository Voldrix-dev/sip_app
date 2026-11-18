#!/bin/bash
# chmod +x install_pjsua2_manjaro.sh

set -euo pipefail

# ----------------------------
# Настройки
# ----------------------------
PY_ENV="$HOME/.venv"
PJ_DIR="$HOME/pjproject"
PJ_VERSION="2.15"

echo "======================================"
echo " PJSUA2 INSTALL (Manjaro / Arch FIX) "
echo "======================================"

# ----------------------------
# Проверка запуска
# ----------------------------
if [ "$EUID" -eq 0 ]; then
    echo "[ERROR] Не запускайте этот скрипт через sudo."
    echo "[ERROR] Запустите: ./install_pjsua2_manjaro.sh"
    exit 1
fi

echo "[INFO] Проверяем доступ к sudo..."
sudo -v

# ----------------------------
# 1. Зависимости
# ----------------------------
echo "[1/7] Установка зависимостей..."

sudo pacman -Sy --needed \
    base-devel git wget curl swig \
    python python-pip \
    openssl alsa-lib v4l-utils \
    ffmpeg opus x264 \
    sdl2 sdl2_gfx sdl2_image sdl2_mixer sdl2_net sdl2_ttf \
    speex speexdsp

# ----------------------------
# 2. Очистка и создание venv
# ----------------------------
echo "[2/7] Подготовка Python venv..."

if [ -d "$PY_ENV" ]; then
    echo "[INFO] Удаляем старое виртуальное окружение..."
    if [ -O "$PY_ENV" ]; then
        rm -rf "$PY_ENV"
    else
        echo "[INFO] Venv принадлежит другому пользователю — удаляем через sudo..."
        sudo rm -rf "$PY_ENV"
    fi
fi

mkdir -p "$(dirname "$PY_ENV")"

echo "[INFO] Создаём Python venv..."
python -m venv "$PY_ENV"

echo "[INFO] Обновляем pip, setuptools и wheel..."
"$PY_ENV/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[INFO] Устанавливаем Python-зависимости..."
"$PY_ENV/bin/python" -m pip install pyside6 qtawesome evdev

# Используем Python из venv для всей дальнейшей сборки Python bindings
export PATH="$PY_ENV/bin:$PATH"
export PYTHON="$PY_ENV/bin/python"

echo "[INFO] Python: $(which python3)"
echo "[INFO] Python version: $(python3 --version)"
echo "[INFO] setuptools: $(python3 -c 'import setuptools; print(setuptools.__version__)')"

# ----------------------------
# 3. Очистка и скачивание pjproject
# ----------------------------
echo "[3/7] Подготовка pjproject..."

if [ -d "$PJ_DIR" ]; then
    echo "[INFO] Удаляем старую директорию pjproject..."
    if [ -O "$PJ_DIR" ]; then
        rm -rf "$PJ_DIR"
    else
        echo "[INFO] Директория принадлежит другому пользователю — удаляем через sudo..."
        sudo rm -rf "$PJ_DIR"
    fi
fi

mkdir -p "$PJ_DIR"
cd "$PJ_DIR"

ARCHIVE="pjproject-${PJ_VERSION}.tar.gz"
SRC="pjproject-${PJ_VERSION}"
URL="https://github.com/pjsip/pjproject/archive/refs/tags/${PJ_VERSION}.tar.gz"

echo "[INFO] Скачиваем pjproject $PJ_VERSION..."
wget -O "$ARCHIVE" "$URL"

echo "[INFO] Распаковываем pjproject..."
tar -xzf "$ARCHIVE"

cd "$SRC"

# ----------------------------
# 4. FIX ENV + compile-time limits
# ----------------------------
echo "[4/7] Настройка окружения сборки..."

export CFLAGS="-fPIC"
export CXXFLAGS="-fPIC"
export LDFLAGS="-L/usr/lib"
export PKG_CONFIG_PATH="/usr/lib/pkgconfig:/usr/share/pkgconfig"

echo "[INFO] Устанавливаем compile-time лимиты..."

cat > pjlib/include/pj/config_site.h <<'EOF'
#define PJSUA_MAX_CALLS 32
#define PJSUA_MAX_CONF_PORTS 256
EOF

echo "[INFO] config_site.h:"
cat pjlib/include/pj/config_site.h

# ----------------------------
# 5. configure
# ----------------------------
echo "[5/7] Конфигурация pjproject..."

./configure --enable-shared \
    --disable-speex \
    --disable-speex-aec

# ----------------------------
# 6. build
# ----------------------------
echo "[6/7] Сборка pjproject..."

make dep
make -j"$(nproc)"

# ----------------------------
# 7. install + Python bindings
# ----------------------------
echo "[7/7] Установка pjproject..."

sudo make install

echo "[7.1/7] Регистрация библиотек в системе..."

echo "/usr/local/lib" | sudo tee /etc/ld.so.conf.d/pjsua2.conf > /dev/null
sudo ldconfig

echo "[7.2/7] Настройка runtime path..."

export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"

echo "[INFO] LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

# ----------------------------
# 7.3 Python bindings
# ----------------------------
echo "[7.3/7] Сборка Python bindings..."

cd pjsip-apps/src/swig/python

echo "[INFO] Python для SWIG: $(which python3)"
echo "[INFO] Python version: $(python3 --version)"
echo "[INFO] Проверка setuptools..."

python3 -c "import setuptools; print('setuptools OK:', setuptools.__version__)"

echo "[INFO] Очистка предыдущей сборки bindings..."
make clean || true

echo "[INFO] Сборка SWIG Python bindings..."
make -j"$(nproc)"

# ----------------------------
# 7.4 Установка pjsua2
# ----------------------------
echo "[7.4/7] Установка pjsua2 в Python venv..."

"$PY_ENV/bin/python" -m pip install --force-reinstall .

# ----------------------------
# TEST
# ----------------------------
echo ""
echo "=============================="
echo " TEST IMPORT "
echo "=============================="

"$PY_ENV/bin/python" - <<'EOF'
import pjsua2
print("PJSUA2 OK - импорт работает")
print("Module:", pjsua2.__file__)
EOF

echo ""
echo "=============================="
echo " BUILD LIMITS "
echo "=============================="

echo "[INFO] config_site.h:"
cat "$PJ_DIR/$SRC/pjlib/include/pj/config_site.h"

echo ""
echo "=============================="
echo " DONE "
echo "=============================="
echo "Activate:"
echo "source $PY_ENV/bin/activate"
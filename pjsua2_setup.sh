#!/bin/bash
# --- chmod +x install_pjsua2.sh ---

set -e

# --- Проверка запуска от обычного пользователя ---
if [ "$EUID" -eq 0 ]; then
    echo "[ERROR] Не запускайте этот скрипт через sudo."
    echo "[ERROR] Запустите: ./install_pjsua2.sh"
    exit 1
fi

# --- Настройки ---
PY_ENV="$HOME/.venv"
PJ_DIR="$HOME/pjproject"
PJ_VERSION="2.15"

# --- Проверка sudo ---
echo "[INFO] Проверяем доступ к sudo..."
sudo -v

# --- Настройка доступа к /dev/uinput ---
echo "[INFO] Настраиваем доступ к /dev/uinput..."

UINPUT_RULE="/etc/udev/rules.d/99-uinput.rules"

if ! getent group input >/dev/null; then
    echo "[INFO] Группа input не существует — создаём..."
    sudo groupadd input
fi

if ! id -nG "$USER" | grep -qw input; then
    echo "[INFO] Добавляем пользователя $USER в группу input..."
    sudo usermod -aG input "$USER"
fi

echo "[INFO] Создаём правило udev для /dev/uinput..."
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee "$UINPUT_RULE" >/dev/null

echo "[INFO] Перезагружаем правила udev..."
sudo udevadm control --reload-rules
sudo udevadm trigger

# --- Очистка старого виртуального окружения ---
echo "[INFO] Очищаем старое виртуальное окружение..."

if [ -d "$PY_ENV" ]; then
    if [ -O "$PY_ENV" ]; then
        rm -rf "$PY_ENV"
    else
        echo "[INFO] Виртуальное окружение принадлежит другому пользователю — удаляем через sudo..."
        sudo rm -rf "$PY_ENV"
    fi
fi

echo "[INFO] Создаём виртуальное окружение Python..."
python3 -m venv "$PY_ENV"

echo "[INFO] Настраиваем Python venv..."

export PATH="$PY_ENV/bin:$PATH"
export PYTHON="$PY_ENV/bin/python"

echo "[INFO] Python: $(which python3)"
echo "[INFO] Python version: $(python3 --version)"

echo "[INFO] Обновляем pip, setuptools и wheel..."
"$PY_ENV/bin/python" -m pip install --upgrade pip setuptools wheel

echo "[INFO] Проверяем setuptools..."
"$PY_ENV/bin/python" -c "import setuptools; print('setuptools OK:', setuptools.__version__)"

echo "[INFO] Устанавливаем Python-зависимости..."
"$PY_ENV/bin/python" -m pip install pyside6 qtawesome evdev

# --- Установка зависимостей ---
echo "[INFO] Устанавливаем зависимости для сборки PJSIP и Python биндингов..."

sudo apt-get update

sudo apt-get install -y \
    build-essential git wget curl swig \
    libasound2-dev libssl-dev libv4l-dev \
    libsdl2-dev libsdl2-gfx-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-net-dev libsdl2-ttf-dev \
    libx264-dev libavformat-dev libavcodec-dev libavdevice-dev libavfilter-dev libswresample-dev libavutil-dev \
    libavcodec-extra libopus-dev libopencore-amrwb-dev libopencore-amrnb-dev libvo-amrwbenc-dev \
    python3-dev

# --- Очистка старых исходников pjproject ---
echo "[INFO] Очищаем старую директорию pjproject..."

if [ -d "$PJ_DIR" ]; then
    if [ -O "$PJ_DIR" ]; then
        rm -rf "$PJ_DIR"
    else
        echo "[INFO] Директория принадлежит другому пользователю — удаляем через sudo..."
        sudo rm -rf "$PJ_DIR"
    fi
fi

mkdir -p "$PJ_DIR"
cd "$PJ_DIR"

# --- Скачивание исходников ---
echo "[INFO] Скачиваем pjproject $PJ_VERSION с GitHub..."

wget -q "https://github.com/pjsip/pjproject/archive/refs/tags/$PJ_VERSION.tar.gz" \
    -O "pjproject-$PJ_VERSION.tar.gz"

echo "[INFO] Распаковываем pjproject..."

tar -xzf "pjproject-$PJ_VERSION.tar.gz"

cd "pjproject-$PJ_VERSION"

# --- Настройка compile-time лимитов ---
echo "[INFO] Настраиваем compile-time лимиты PJSUA2..."

cat > pjlib/include/pj/config_site.h <<'EOF'
#define PJSUA_MAX_CALLS 32
#define PJSUA_MAX_CONF_PORTS 256
EOF

echo "[INFO] config_site.h:"
cat pjlib/include/pj/config_site.h

# --- Сборка pjproject ---
echo "[INFO] Настраиваем pjproject..."

CFLAGS="-fPIC" CXXFLAGS="-fPIC" ./configure --enable-shared

echo "[INFO] Выполняем make dep..."

make dep

echo "[INFO] Компилируем pjproject..."

make -j"$(nproc)"

echo "[INFO] Устанавливаем pjproject..."

sudo make install

echo "[INFO] Обновляем кэш библиотек..."

sudo ldconfig

# --- Переменные окружения для pjsua2 ---
export LD_LIBRARY_PATH="/usr/local/lib:${LD_LIBRARY_PATH:-}"
export PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}"

echo "[INFO] LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
echo "[INFO] PKG_CONFIG_PATH=$PKG_CONFIG_PATH"

# --- Сборка Python биндингов ---
echo "[INFO] Собираем Python биндинги pjsua2..."

cd pjsip-apps/src/swig/python

echo "[INFO] Python для SWIG: $(which python3)"
echo "[INFO] Python version: $(python3 --version)"

echo "[INFO] Проверяем setuptools..."

python3 -c "import setuptools; print('setuptools OK:', setuptools.__version__)"

echo "[INFO] Очищаем старую сборку Python bindings..."

make clean || true

echo "[INFO] Компилируем Python bindings..."

make -j"$(nproc)"

# --- Установка pjsua2 в venv ---
echo "[INFO] Устанавливаем pjsua2 в виртуальное окружение..."

"$PY_ENV/bin/python" -m pip install --force-reinstall .

# --- Проверка ---
echo "[INFO] Проверяем импорт pjsua2..."

"$PY_ENV/bin/python" -c "import pjsua2; print('PJSUA2 успешно установлен и импортируется!'); print('Module:', pjsua2.__file__)"

# --- Финальная информация ---
echo ""
echo "======================================"
echo " PJSUA2 УСТАНОВКА ЗАВЕРШЕНА"
echo "======================================"

echo "[INFO] Compile-time limits:"
echo "       PJSUA_MAX_CALLS=32"
echo "       PJSUA_MAX_CONF_PORTS=256"

echo ""
echo "[INFO] Активировать окружение:"
echo "source $PY_ENV/bin/activate"
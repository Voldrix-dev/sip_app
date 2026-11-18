#!/usr/bin/env bash
# Установка постоянного loopback sink для PipeWire / PulseAudio (с проверкой версии WirePlumber)

set -euo pipefail

SINK_NAME="sipbot_sink"
SINK_DESCRIPTION="SIPBotSink"

log() { echo -e "\033[0;32m[OK]\033[0m $1"; }
warn() { echo -e "\033[1;33m[WARN]\033[0m $1"; }
error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; exit 1; }

create_sink_now() {
    if command -v pactl >/dev/null 2>&1; then
        pactl load-module module-null-sink \
            sink_name="$SINK_NAME" \
            sink_properties=device.description="$SINK_DESCRIPTION" >/dev/null 2>&1 || true
        log "Sink создан в текущей сессии"
    else
        warn "pactl не найден"
    fi
}

# Проверяем версию WirePlumber
get_wireplumber_version() {
    if command -v wireplumber >/dev/null 2>&1; then
        wireplumber --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1
    fi
}

WIREPLUMBER_VERSION=$(get_wireplumber_version)
if [ -n "$WIREPLUMBER_VERSION" ]; then
    log "WirePlumber версия: $WIREPLUMBER_VERSION"
    # WirePlumber 0.5+ не поддерживает Lua-конфиги
    if printf '%s\n' "0.5" "$WIREPLUMBER_VERSION" | sort -V | head -1 | grep -q "0.5"; then
        WP_VERSION_GE_05=1
        log "WirePlumber >= 0.5 — Lua-конфиги НЕ поддерживаются"
    else
        WP_VERSION_GE_05=0
        log "WirePlumber < 0.5 — Lua-конфиги поддерживаются"
    fi
else
    WP_VERSION_GE_05=1
    warn "WirePlumber не найден, предполагаем >= 0.5"
fi

setup_pipewire() {
    local pw_dir="$HOME/.config/pipewire/pipewire-pulse.conf.d"
    mkdir -p "$pw_dir"

    # pipewire-pulse.conf.d — работает во всех версиях
    cat > "$pw_dir/99-sipbot-sink.conf" <<EOF
context.modules = [
    { name = libpipewire-module-null-sink
      args = {
        sink_name = "$SINK_NAME"
        sink_properties = {
          device.description = "$SINK_DESCRIPTION"
        }
      }
    }
]
EOF
    log "PipeWire-Pulse конфиг создан: $pw_dir/99-sipbot-sink.conf"

    # WirePlumber < 0.5 — можно добавить Lua-конфиг (опционально)
    if [ "$WP_VERSION_GE_05" -eq 0 ]; then
        local wp_dir="$HOME/.config/wireplumber/main.lua.d"
        mkdir -p "$wp_dir"
        cat > "$wp_dir/99-sipbot-sink.lua" <<EOF
-- SIP Bot loopback sink (WirePlumber < 0.5)
table.insert(alsa_monitor.rules, {
  matches = {
    { { "node.name", "matches", "$SINK_NAME" } },
  },
  apply_properties = {
    ["node.description"] = "$SINK_DESCRIPTION",
  },
})
EOF
        log "WirePlumber Lua-конфиг создан: $wp_dir/99-sipbot-sink.lua"
    else
        log "WirePlumber >= 0.5 — Lua-конфиг пропущен"
    fi

    # Systemd user service — самый надёжный способ (все версии)
    local service_dir="$HOME/.config/systemd/user"
    local service_file="$service_dir/sipbot-sink.service"
    mkdir -p "$service_dir"

    cat > "$service_file" <<EOF
[Unit]
Description=SIP Bot Loopback Sink
After=pipewire-pulse.service
Requires=pipewire-pulse.service

[Service]
Type=oneshot
ExecStart=/usr/bin/pactl load-module module-null-sink sink_name=$SINK_NAME sink_properties=device.description=$SINK_DESCRIPTION
RemainAfterExit=yes

[Install]
WantedBy=pipewire-pulse.service
EOF

    log "Systemd user service создан: $service_file"

    systemctl --user daemon-reload
    systemctl --user enable --now sipbot-sink.service 2>/dev/null || true
    log "Service включён и запущен"
}

setup_pulseaudio() {
    local pa_dir="$HOME/.config/pulse"
    local pa_file="$pa_dir/default.pa"

    mkdir -p "$pa_dir"

    if [ ! -f "$pa_file" ]; then
        if [ -f /etc/pulse/default.pa ]; then
            cp /etc/pulse/default.pa "$pa_file"
        else
            echo ".include /etc/pulse/default.pa" > "$pa_file"
        fi
    fi

    if grep -q "module-null-sink.*sink_name=$SINK_NAME" "$pa_file"; then
        log "Sink уже в $pa_file"
    else
        echo "" >> "$pa_file"
        echo "# SIP Bot loopback sink" >> "$pa_file"
        echo "load-module module-null-sink sink_name=$SINK_NAME sink_properties=device.description=$SINK_DESCRIPTION" >> "$pa_file"
        log "Добавлено в $pa_file"
    fi

    systemctl --user restart pulseaudio 2>/dev/null || pulseaudio -k 2>/dev/null || true
    log "PulseAudio перезапущен"
}

# Определяем звуковой сервер
if systemctl --user is-active pipewire-pulse >/dev/null 2>&1 || pgrep -x pipewire >/dev/null 2>&1; then
    log "Обнаружен PipeWire"
    setup_pipewire
elif systemctl --user is-active pulseaudio >/dev/null 2>&1 || pgrep -x pulseaudio >/dev/null 2>&1; then
    log "Обнаружен PulseAudio"
    setup_pulseaudio
else
    warn "Звуковой сервер не определён, пробуем PipeWire"
    setup_pipewire
fi

create_sink_now

log "Готово. Sink '$SINK_NAME' будет создаваться автоматически."
echo "Проверить: pactl list short sinks | grep $SINK_NAME"
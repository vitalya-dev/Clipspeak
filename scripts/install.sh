#!/usr/bin/env bash
#
# Поднимает Clipspeak «с нуля» на свежем Arch Linux.
#
# Что делает:
#   1. доустанавливает системные пакеты (python, ffmpeg, libpulse, wl-clipboard);
#   2. создаёт .venv (клиент) и tts/.venv (torch + numpy);
#   3. скачивает модели Silero (scripts/fetch-models.sh);
#   4. генерирует и включает systemd-юниты silero-en / silero-ru;
#   5. отключает старые piper-* юниты, если они были.
#
# Скрипт идемпотентен: повторный запуск ничего не ломает.
#
# Примеры:
#   ./scripts/install.sh
#   ./scripts/install.sh --en-speaker en_21 --ru-speaker baya
#   ./scripts/install.sh --skip-pacman
#   ./scripts/install.sh --set-shortcut

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DESKTOP_FILE="$HOME/.local/share/applications/net.local.python.desktop"

EN_SPEAKER="en_0"
RU_SPEAKER="aidar"
THREADS="4"
DO_PACMAN=1
SET_SHORTCUT=0

REQUIRED_PKGS=(python ffmpeg libpulse wl-clipboard)

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --en-speaker) EN_SPEAKER="$2"; shift 2 ;;
        --ru-speaker) RU_SPEAKER="$2"; shift 2 ;;
        --threads)    THREADS="$2"; shift 2 ;;
        --skip-pacman) DO_PACMAN=0; shift ;;
        --set-shortcut) SET_SHORTCUT=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage; exit 2 ;;
    esac
done

log()  { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[1;33m[warn]\033[0m %s\n' "$*"; }

# --- 1. Системные пакеты -----------------------------------------------------
log "Проверяем системные пакеты"
if ! command -v pacman >/dev/null 2>&1; then
    warn "pacman не найден — это не Arch? Пропускаем установку пакетов"
else
    missing=()
    for pkg in "${REQUIRED_PKGS[@]}"; do
        if pacman -Q "$pkg" >/dev/null 2>&1; then
            ok "$pkg"
        else
            missing+=("$pkg")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        if [[ "$DO_PACMAN" -eq 1 ]]; then
            log "Ставим: ${missing[*]}"
            sudo pacman -S --needed --noconfirm "${missing[@]}"
        else
            warn "Не хватает пакетов: ${missing[*]} (--skip-pacman)"
        fi
    fi
fi

# --- 2. Виртуальные окружения ------------------------------------------------
log "Создаём .venv (клиент)"
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$REPO_DIR/.venv/bin/pip" install --quiet -r "$REPO_DIR/requirements.txt"
ok "клиентские зависимости установлены"

log "Создаём tts/.venv (torch CPU + numpy)"
python3 -m venv "$REPO_DIR/tts/.venv"
TTSPY="$REPO_DIR/tts/.venv/bin/python"
"$TTSPY" -m pip install --quiet --upgrade pip
# torch берём с CPU-индекса: ~200 МБ вместо ~3 ГБ CUDA-сборки.
"$TTSPY" -m pip install --quiet --index-url https://download.pytorch.org/whl/cpu torch
"$TTSPY" -m pip install --quiet -r "$REPO_DIR/tts/requirements.txt"
"$TTSPY" -c "import torch, numpy; print('  torch', torch.__version__, '/ numpy', numpy.__version__)"
ok "TTS-зависимости установлены"

# --- 3. Модели ---------------------------------------------------------------
log "Скачиваем модели"
"$REPO_DIR/scripts/fetch-models.sh"

# --- 4. systemd-юниты --------------------------------------------------------
log "Генерируем systemd-юниты в $UNIT_DIR"
mkdir -p "$UNIT_DIR"

render_unit() {
    local template="$1" output="$2" speaker="$3" lang="$4"
    sed -e "s|@REPO@|$REPO_DIR|g" \
        -e "s|@SPEAKER@|$speaker|g" \
        -e "s|@LANG@|$lang|g" \
        -e "s|@THREADS@|$THREADS|g" \
        "$template" > "$UNIT_DIR/$output"
    ok "$output"
}

render_unit "$REPO_DIR/systemd/silero-en.service.in" "silero-en.service" "$EN_SPEAKER" "en"
render_unit "$REPO_DIR/systemd/silero-ru.service.in" "silero-ru.service" "$RU_SPEAKER" "ru"

systemctl --user daemon-reload

# Отключаем старые piper-юниты, если они остались с прошлой установки.
for legacy in piper-en.service piper-ru.service; do
    if systemctl --user cat "$legacy" >/dev/null 2>&1; then
        systemctl --user disable --now "$legacy" 2>/dev/null || true
        warn "старый $legacy отключён"
    fi
done

systemctl --user enable silero-en.service silero-ru.service >/dev/null
systemctl --user restart silero-en.service silero-ru.service
ok "сервисы запущены"

wait_ready() {
    local port="$1" name="$2"
    for _ in $(seq 1 90); do
        if curl -sf --max-time 2 "http://127.0.0.1:$port/" >/dev/null 2>&1; then
            ok "$name готов (порт $port)"
            return 0
        fi
        sleep 1
    done
    warn "$name не ответил за 90 сек — см. journalctl --user -u $name"
    return 1
}

log "Ждём загрузки моделей"
wait_ready 5001 silero-en.service || true
wait_ready 5002 silero-ru.service || true

# --- 5. KDE-шорткат (опционально) -------------------------------------------
if [[ "$SET_SHORTCUT" -eq 1 ]]; then
    log "Обновляем KDE-шорткат"
    if [[ -f "$DESKTOP_FILE" ]]; then
        backup="$DESKTOP_FILE.bak.$(date +%Y%m%d-%H%M%S)"
        cp "$DESKTOP_FILE" "$backup"
        sed -i -E "s|^Exec=.*|Exec=$REPO_DIR/.venv/bin/python $REPO_DIR/clipspeak.py|" "$DESKTOP_FILE"
        ok "Exec теперь указывает на venv (бэкап: $backup)"
    else
        warn "$DESKTOP_FILE не найден — пропускаем"
    fi
fi

# --- Итог --------------------------------------------------------------------
log "Готово"
echo "  EN: http://127.0.0.1:5001  (модель v3_en, голос $EN_SPEAKER)"
echo "  RU: http://127.0.0.1:5002  (модель v5_6_ru, голос $RU_SPEAKER)"
echo
echo "Проверка:  ./scripts/healthcheck.sh"
echo "Статус:    systemctl --user status silero-en silero-ru"
echo "Логи:      journalctl --user -u silero-ru -f"
echo
"$REPO_DIR/scripts/healthcheck.sh"

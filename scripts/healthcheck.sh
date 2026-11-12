#!/usr/bin/env bash
#
# Проверяет, что оба TTS-сервера живы и реально синтезируют звук.
# Возвращает ненулевой код, если что-то не так.
#
#   ./scripts/healthcheck.sh

set -uo pipefail

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

FAILED=0

check_service() {
    local unit="$1"
    if systemctl --user is-active --quiet "$unit"; then
        printf '  \033[1;32m[ok]\033[0m %s active\n' "$unit"
    else
        printf '  \033[1;31m[FAIL]\033[0m %s не active\n' "$unit"
        systemctl --user status "$unit" --no-pager -n 5 2>&1 | sed 's/^/        /'
        FAILED=1
    fi
}

check_tts() {
    local name="$1" port="$2" text="$3"
    local url="http://127.0.0.1:$port"

    # 1. health endpoint (сервис может ещё грузить модель после рестарта)
    local health=""
    for _ in $(seq 1 60); do
        health="$(curl -s --max-time 5 "$url/")" || true
        if [[ "$health" == *'"status": "ok"'* || "$health" == *'"status":"ok"'* ]]; then
            break
        fi
        sleep 1
    done
    if [[ "$health" == *'"status": "ok"'* || "$health" == *'"status":"ok"'* ]]; then
        printf '  \033[1;32m[ok]\033[0m %s GET / отвечает\n' "$name"
    else
        printf '  \033[1;31m[FAIL]\033[0m %s GET / не отвечает: %s\n' "$name" "$health"
        FAILED=1
        return
    fi

    # 2. реальный синтез
    local payload code out="$TMP_DIR/$port.wav"
    payload="$(printf '{"text":%s,"length_scale":1.0}' "$(printf '%s' "$text" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
    code="$(curl -s --max-time 120 -o "$out" -w '%{http_code}' \
        -H 'Content-Type: application/json' -d "$payload" "$url/" || echo 000)"

    if [[ "$code" == "200" && "$(head -c 4 "$out" 2>/dev/null)" == "RIFF" ]]; then
        local size
        size="$(stat -c %s "$out")"
        printf '  \033[1;32m[ok]\033[0m %s синтез: HTTP 200, %s байт WAV\n' "$name" "$size"
    else
        printf '  \033[1;31m[FAIL]\033[0m %s синтез: HTTP %s, не WAV\n' "$name" "$code"
        FAILED=1
    fi
}

echo "Clipspeak healthcheck"
check_service silero-en.service
check_service silero-ru.service
check_tts "EN (5001)" 5001 "Health check for the English voice."
check_tts "RU (5002)" 5002 "Проверка русского голоса."

echo
if [[ "$FAILED" -eq 0 ]]; then
    printf '\033[1;32mВсё работает.\033[0m\n'
    exit 0
else
    printf '\033[1;31mЕсть проблемы, см. вывод выше.\033[0m\n'
    exit 1
fi

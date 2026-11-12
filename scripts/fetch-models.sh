#!/usr/bin/env bash
#
# Скачивает модели Silero и проверяет контрольные суммы.
# Повторный запуск не перекачивает уже скачанные файлы.
#
# Использование:
#   ./scripts/fetch-models.sh            # обе модели
#   ./scripts/fetch-models.sh --en       # только английская
#   ./scripts/fetch-models.sh --ru       # только русская

set -euo pipefail

REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$REPO_DIR/tts/models"

SILERO_RU_URL="https://models.silero.ai/models/tts/ru/v5_6_ru.pt"
SILERO_RU_SHA256="04cc9b1d08ce38bcaa9d02c488d80d3e6806d17f7eaa7c95982f36a3cbf50b2e"

SILERO_EN_URL="https://models.silero.ai/models/tts/en/v3_en.pt"
SILERO_EN_SHA256="02b71034d9f13bc4001195017bac9db1c6bb6115e03fea52983e8abcff13b665"

DO_EN=1
DO_RU=1

for arg in "$@"; do
    case "$arg" in
        --en) DO_RU=0 ;;
        --ru) DO_EN=0 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Неизвестный аргумент: $arg" >&2
            exit 2
            ;;
    esac
done

download() {
    local url="$1" dest="$2" sha="$3" name
    name="$(basename "$dest")"

    if [[ -f "$dest" ]]; then
        if echo "$sha  $dest" | sha256sum -c --status -; then
            echo "[ok] $name уже скачана и совпадает по sha256"
            return 0
        fi
        echo "[warn] $name есть, но sha256 не совпадает — перекачиваем"
    fi

    echo "[..] Качаем $name"
    curl --fail --location --retry 3 --retry-delay 2 \
         --connect-timeout 20 --progress-bar \
         --output "$dest.part" "$url"

    echo "$sha  $dest.part" | sha256sum -c --status - \
        || { rm -f "$dest.part"; echo "[error] sha256 не совпал для $name" >&2; exit 1; }

    mv "$dest.part" "$dest"
    echo "[ok] $name: $(du -h "$dest" | cut -f1)"
}

mkdir -p "$MODELS_DIR"

if [[ "$DO_EN" -eq 1 ]]; then
    download "$SILERO_EN_URL" "$MODELS_DIR/v3_en.pt" "$SILERO_EN_SHA256"
fi

if [[ "$DO_RU" -eq 1 ]]; then
    download "$SILERO_RU_URL" "$MODELS_DIR/v5_6_ru.pt" "$SILERO_RU_SHA256"
fi

echo
echo "Модели в $MODELS_DIR:"
ls -lh "$MODELS_DIR"/*.pt

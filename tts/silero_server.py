#!/usr/bin/env python3
"""
HTTP-сервер Silero TTS с контрактом, совместимым с Piper.

Принимает POST с JSON:
    {"text": "...", "length_scale": 0.5, "speaker": "aidar"}

и возвращает WAV (audio/wav).

length_scale трактуется как у Piper: 0.5 = в два раза быстрее,
1.0 = обычная скорость, 2.0 = в два раза медленнее. Темп меняется
через ffmpeg atempo, поэтому высота голоса не меняется.

Примеры запуска (см. systemd/*.service.in):

    silero_server.py --model models/v5_6_ru.pt --speaker aidar --port 5002
    silero_server.py --model models/v3_en.pt  --speaker en_0  --port 5001
"""

import argparse
import io
import json
import logging
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch

logger = logging.getLogger("silero-server")

SCRIPT_DIR = Path(__file__).resolve().parent

# Модель и её состояние инициализируются в main() и используются в handler'ах.
MODEL = None
MODEL_LOCK = threading.Lock()
DEFAULT_SPEAKER = "aidar"
SAMPLE_RATE = 48000


def resolve_path(path: str) -> Path:
    """Относительные пути --model считаем от каталога скрипта, а не от cwd."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = SCRIPT_DIR / candidate
    return candidate.resolve()


def load_model(path: Path):
    logger.info("Загружаем модель: %s", path)
    if not path.is_file():
        raise SystemExit(
            f"Модель не найдена: {path}\n"
            f"Запусти ./scripts/fetch-models.sh, чтобы скачать её."
        )
    model = torch.package.PackageImporter(str(path)).load_pickle("tts_models", "model")
    model.to(torch.device("cpu"))
    logger.info("Модель загружена. Голоса (%d): %s", len(model.speakers),
                ", ".join(model.speakers[:10]) + (" ..." if len(model.speakers) > 10 else ""))
    return model


def tensor_to_wav_bytes(audio: torch.Tensor, sample_rate: int) -> bytes:
    """Преобразует тензор float32 [-1, 1] в WAV (PCM 16 bit mono)."""
    pcm = (audio.clamp(-1.0, 1.0).cpu().numpy() * 32767.0).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def apply_speed(wav_bytes: bytes, length_scale: float) -> bytes:
    """Меняет темп через ffmpeg atempo, сохраняя высоту голоса."""
    if length_scale is None or length_scale <= 0:
        return wav_bytes
    speed = 1.0 / length_scale
    if abs(speed - 1.0) < 0.01:
        return wav_bytes

    # atempo в свежих ffmpeg принимает 0.5..100. Для крайних значений
    # собираем цепочку фильтров.
    filters = []
    remaining = speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")

    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "wav", "-i", "pipe:0",
        "-filter:a", ",".join(filters),
        "-acodec", "pcm_s16le", "-f", "wav", "pipe:1",
    ]
    logger.info("Скорость: length_scale=%s -> %s", length_scale, ",".join(filters))
    result = subprocess.run(cmd, input=wav_bytes, capture_output=True, check=True)
    return result.stdout


def synthesize(text: str, speaker: str, length_scale: float) -> bytes:
    if not text or not text.strip():
        raise ValueError("Пустой текст")
    if speaker not in MODEL.speakers:
        logger.warning("Неизвестный голос %r, используем %r", speaker, DEFAULT_SPEAKER)
        speaker = DEFAULT_SPEAKER

    # apply_tts не потокобезопасен — синтезируем по одному запросу за раз.
    with MODEL_LOCK:
        audio = MODEL.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=SAMPLE_RATE,
        )

    wav_bytes = tensor_to_wav_bytes(audio, SAMPLE_RATE)
    return apply_speed(wav_bytes, length_scale)


class Handler(BaseHTTPRequestHandler):
    server_version = "SileroTTS/1.0"

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Простой health-check.
        self._send_json(200, {
            "status": "ok",
            "speakers": MODEL.speakers,
            "default_speaker": DEFAULT_SPEAKER,
            "sample_rate": SAMPLE_RATE,
        })

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
            text = payload.get("text", "")
            speaker = payload.get("speaker") or DEFAULT_SPEAKER
            length_scale = float(payload.get("length_scale", 1.0))
        except (ValueError, json.JSONDecodeError) as e:
            self._send_json(400, {"error": f"Некорректный запрос: {e}"})
            return

        logger.info("Синтез (%s, %d символов): %s", speaker, len(text), text[:120])

        try:
            wav_bytes = synthesize(text, speaker, length_scale)
        except Exception as e:
            logger.exception("Ошибка синтеза")
            self._send_json(500, {"error": f"{type(e).__name__}: {e}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.end_headers()
        try:
            self.wfile.write(wav_bytes)
        except BrokenPipeError:
            # Клиент отвалился (например, Clipspeak убит) — это не наша проблема.
            logger.debug("Клиент разорвал соединение")

    def log_message(self, fmt, *args):
        logger.debug("%s - %s", self.address_string(), fmt % args)


def main():
    global MODEL, DEFAULT_SPEAKER, SAMPLE_RATE

    parser = argparse.ArgumentParser(description="Silero TTS HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5002)
    parser.add_argument("--model", default="models/v5_6_ru.pt",
                        help="путь к .pt (относительно tts/)")
    parser.add_argument("--speaker", default=DEFAULT_SPEAKER)
    parser.add_argument("--sample-rate", type=int, default=SAMPLE_RATE)
    parser.add_argument("--threads", type=int, default=4,
                        help="torch.set_num_threads")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    DEFAULT_SPEAKER = args.speaker
    SAMPLE_RATE = args.sample_rate
    torch.set_num_threads(args.threads)

    MODEL = load_model(resolve_path(args.model))

    if DEFAULT_SPEAKER not in MODEL.speakers:
        logger.warning("Голос %r отсутствует в модели, доступны: %s",
                       DEFAULT_SPEAKER, ", ".join(MODEL.speakers[:10]))
        DEFAULT_SPEAKER = MODEL.speakers[0]
        logger.warning("Используем %r", DEFAULT_SPEAKER)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("Слушаем http://%s:%d (голос по умолчанию: %s)", args.host, args.port, DEFAULT_SPEAKER)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

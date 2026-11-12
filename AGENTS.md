# AGENTS.md

Инструкции для человека и для ИИ-агента: как устроен Clipspeak, как поднять
его на свежей системе и как чинить типовые поломки.

## Что это

Clipspeak читает текст из буфера обмена (Wayland), чистит его, разбивает на
предложения и озвучивает через локальные HTTP-серверы Silero TTS. Аудио
играет через PipeWire/PulseAudio (`paplay`).

```
wl-paste ──> clean_text() ──> detect_language() ──> split_into_sentences()
                                                          │
                        ┌─────────────────────────────────┘
                        ▼
        download_audio_worker (поток)             play_audio_worker (главный поток)
        POST / {text,length_scale}                очередь путей к .wav
             │                                         │
             ├─ EN -> 127.0.0.1:5001 (v3_en)           ├─ paplay
             └─ RU -> 127.0.0.1:5002 (v5_6_ru)         └─ rm temp-файла
```

Синтез идёт параллельно с воспроизведением: пока играет первое предложение,
следующее уже генерируется.

## Порты и модели

| Порт | systemd unit | Модель | Голос по умолчанию |
|---|---|---|---|
| 5001 | `silero-en.service` | `tts/models/v3_en.pt` (55 МБ) | `en_0` (всего 118 голосов `en_0`…`en_117`) |
| 5002 | `silero-ru.service` | `tts/models/v5_6_ru.pt` (141 МБ) | `aidar` (`aidar`, `baya`, `kseniya`, `xenia`, `eugene`) |

Оба сервиса — это один и тот же `tts/silero_server.py` с разными `--model`,
`--speaker` и `--lang` (`en` для 5001, `ru` для 5002).

## Структура репозитория

```
Clipspeak/
├── AGENTS.md
├── clipspeak.py              # основной скрипт
├── requirements.txt          # ftfy, pysbd (для .venv)
├── .venv/                    # клиентский venv (gitignored)
├── tts/
│   ├── silero_server.py      # HTTP-сервер Silero
│   ├── textnorm.py           # числа и символы -> слова перед синтезом
│   ├── requirements.txt      # torch, numpy, num2words
│   ├── .venv/                # TTS venv (gitignored)
│   └── models/               # .pt модели (gitignored, качаются скриптом)
├── tests/
│   └── test_textnorm.py      # юнит-тесты нормализации (unittest)
├── systemd/
│   ├── silero-en.service.in
│   └── silero-ru.service.in
└── scripts/
    ├── install.sh            # полный подъём с нуля
    ├── fetch-models.sh       # только модели (+sha256)
    └── healthcheck.sh        # проверка сервисов, синтеза и чисел
```

## Установка с нуля (свежий Arch Linux)

Требуется: Arch с KDE/Wayland, PipeWire (или PulseAudio), интернет.

```bash
git clone <repo> ~/Projects/Clipspeak
cd ~/Projects/Clipspeak
./scripts/install.sh
./scripts/healthcheck.sh
```

`install.sh` сам:

1. доставит пакеты `python`, `ffmpeg`, `libpulse`, `wl-clipboard` через `sudo pacman`;
2. создаст `.venv` и `tts/.venv`, поставит зависимости (torch — с CPU-индекса);
3. скачает модели с проверкой `sha256`;
4. сгенерирует юниты в `~/.config/systemd/user/` с абсолютным путём репо;
5. выключит старые `piper-en` / `piper-ru`, включит и запустит `silero-en` / `silero-ru`.

Полезные флаги:

```bash
./scripts/install.sh --en-speaker en_21 --ru-speaker baya
./scripts/install.sh --threads 2
./scripts/install.sh --skip-pacman          # пакеты уже стоят
./scripts/install.sh --set-shortcut         # переписать KDE-шорткат на venv-python
```

Скрипт идемпотентен — можно запускать повторно после `git pull`.

### KDE-шорткат

Глобальный хоткей в KDE идёт через
`~/.local/share/applications/net.local.python.desktop`:

```ini
Exec=python /home/vitalya/Projects/Clipspeak/clipspeak.py
```

Проблема: `python` — системный, без `ftfy`/`pysbd`, поэтому работает
fallback-режим. `./scripts/install.sh --set-shortcut` заменит `Exec` на
`/path/to/repo/.venv/bin/python /path/to/repo/clipspeak.py` (с бэкапом файла).
Если шорткат уже настроен — ничего делать не нужно.

## Ежедневное использование

```bash
# скопировать текст в буфер, затем:
./clipspeak.py
```

Или по глобальному хоткею KDE.

Ограничения и поведение:

- читается **PRIMARY selection** (выделение мышью, `wl-paste -p`), а не
  обычный буфер Ctrl+C. Для проверки из терминала:
  `printf 'текст' | wl-copy --primary`, затем запустить скрипт;
- нужен непустой буфер/выделение;
- язык определяется по доле кириллицы: > 20% букв → `ru`, иначе `en`;
- пустые предложения пропускаются;
- временные `.wav` удаляются после проигрывания;
- скрипт завершает другие свои запущенные копии: ищет через
  `pgrep -f clipspeak.py`, но убивает только реальные python-процессы
  (обёртки вроде `timeout`/`bash -c` игнорируются).

## Настройки

В `clipspeak.py`:

| Переменная | Смысл |
|---|---|
| `TTS_URLS` | словарь `язык → URL` (по умолчанию 5001/5002) |
| `TTS_LENGTH_SCALE` | темп: `0.5` = x2 быстрее, `1.0` = обычно, `2.0` = медленнее |
| `HYPHEN_JOIN` | склейка переносов: `""` — склеить слово, `" "` — разделить |

Смена голоса без правки кода:

```bash
./scripts/install.sh --ru-speaker baya
# или вручную:
systemctl --user edit silero-ru   # поменять --speaker
systemctl --user restart silero-ru
```

Список голосов модели:

```bash
curl -s http://127.0.0.1:5002/ | python3 -m json.tool
```

## Числа и нормализация

Токенсет моделей Silero **не содержит цифр** (а русский — ещё и латиницы):

```
v5_6_ru: _~|!+,-.:;?абвгдежзийклмнопрстуфхцчшщъыьэюяё–…
v3_en:   _~| !"',-.:;?abcdefghijklmnopqrstuvwxyz
```

`apply_tts` вырезает всё, чего нет в наборе, поэтому без обработки
«В 1984 году было 25 человек» звучит как «В году было человек», а «5%»
превращается в пустую строку и даёт 500.

Поэтому `tts/silero_server.py` перед синтезом вызывает
`tts/textnorm.py:normalize(text, lang)` (язык задаётся флагом `--lang`).
Что покрыто:

| Что | Пример | Результат (ru) |
|---|---|---|
| целые, разделители тысяч | `1 000 000` | «один миллион» |
| дроби | `3,14` | «три целых четырнадцать сотых» |
| годы + падеж | `в 1984 году` | «в тысяча девятьсот восемьдесят четвёртом году» |
| порядковые с суффиксом | `1-й`, `2-я` | «первый», «вторая» |
| проценты с согласованием | `1%`, `2%`, `5%` | «один процент», «два процента», «пять процентов» |
| диапазоны лет | `1990–1995` | «с … по …» (или «…–… годах») |
| знак номера | `№ 5` | «номер пять» |
| минус | `−5` | «минус пять» |

Для английского — аналогично через `num2words` (`in nineteen eighty-four`,
`three point one four`, `first`).

Ограничения:

* цифры, приклеенные к латинскому слову (`IPv6`), не трогаются;
* валюты (`$`, `€`, `₽`), время `10:30` и длинные числа по цифрам пока не
  обрабатываются;
* латиница в русском голосе по-прежнему выпадает (отдельная задача).

Тесты:

```bash
tts/.venv/bin/python -m unittest discover -s tests -v
```

`scripts/healthcheck.sh` дополнительно синтезирует фразу с цифрами и ту же
фразу словами и сравнивает размер WAV — если числа снова начнут выпадать,
проверка упадёт.

## Проверка и диагностика

```bash
./scripts/healthcheck.sh
systemctl --user status silero-en silero-ru
journalctl --user -u silero-ru -f
ss -ltnp | grep -E ':500[12]'
```

Типовые проблемы:

| Симптом | Причина / решение |
|---|---|
| `502`/connection refused | сервис не запущен: `systemctl --user restart silero-ru` |
| `Модель не найдена` | `./scripts/fetch-models.sh` |
| `paplay: command not found` | `sudo pacman -S libpulse` |
| `wl-paste: command not found` | `sudo pacman -S wl-clipboard` |
| тишина / нет звука | проверить PipeWire: `pactl info`, `paplay /usr/share/sounds/...` |
| `ffmpeg` не найден, но synth работает | ускорение не применяется — `sudo pacman -S ffmpeg` |
| шорткат не видит `ftfy`/`pysbd` | `./scripts/install.sh --set-shortcut` |
| порт занят | `ss -ltnp \| grep 5002`, остановить старый процесс/юнит |
| после обновления Python нет torch | подождать wheel под новую версию или поставить Python 3.14 |

## Модели

В git **не хранятся** (слишком большие). Скачиваются `scripts/fetch-models.sh`
в `tts/models/` с проверкой `sha256`:

| Файл | Размер | sha256 |
|---|---|---|
| `v3_en.pt` | 55 МБ | `02b71034d9f13bc4001195017bac9db1c6bb6115e03fea52983e8abcff13b665` |
| `v5_6_ru.pt` | 141 МБ | `04cc9b1d08ce38bcaa9d02c488d80d3e6806d17f7eaa7c95982f36a3cbf50b2e` |

Источник: <https://models.silero.ai/models/tts/>.

Лицензия моделей Silero — **CC BY-NC 4.0**, то есть только некоммерческое
использование. Для личного чтения буфера подходит, для продукта — нет.

## Для агента: правила работы

- Не коммитить `tts/models/*.pt`, `.venv/`, `tts/.venv/` — они в `.gitignore`.
- Не менять `tts/silero_server.py` без проверки обоих языков: `./scripts/healthcheck.sh`.
- После изменения `clipspeak.py` прогнать хотя бы один синтез на RU и EN.
- После правок `tts/textnorm.py` прогнать
  `tts/.venv/bin/python -m unittest discover -s tests -v` и
  `./scripts/healthcheck.sh`.
- Юниты в `~/.config/systemd/user/` генерируются из `systemd/*.service.in`;
  правки вносить в шаблоны, а не в сгенерированные файлы.
- Скрипты должны оставаться идемпотентными и работать из любого каталога
  (пути считаются от `BASH_SOURCE`, а не от `$PWD`).

## Приложение: откат на Piper

Piper из проекта убран, но вернуть его можно так:

```bash
python3 -m venv ~/Projects/Piper-tts
~/Projects/Piper-tts/bin/pip install piper-tts
cd ~/Projects/Piper-tts
./bin/python -m piper.download_voices en_US-amy-medium ru_RU-denis-medium
./bin/python -m piper.http_server -m en_US-amy-medium --port 5001
./bin/python -m piper.http_server -m ru_RU-denis-medium --port 5002
```

Контракт запроса тот же (`{"text", "length_scale"}`), поэтому `TTS_URLS`
менять не нужно — достаточно переключить порты.

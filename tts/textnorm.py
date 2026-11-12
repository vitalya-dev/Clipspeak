#!/usr/bin/env python3
"""
Нормализация текста перед отправкой в Silero TTS.

Токенсет моделей Silero не содержит цифр: `apply_tts` вырезает всё, чего нет
в наборе символов, поэтому «В 1984 году было 25 человек» звучит как
«В году было человек». Этот модуль заменяет числа словами заранее.

Поддерживается:
  * целые и дробные числа, разделители тысяч (пробелы, запятые для en);
  * отрицательные числа («−5» -> «минус пять»);
  * годы с правильным падежом («в 1984 году» -> «в ... четвёртом году»);
  * порядковые с суффиксом («1-й», «2-я», «1st»);
  * проценты с согласованием («1 процент», «2 процента», «5 процентов»);
  * диапазоны лет («1990–1995» -> «с ... по ...»);
  * знак номера («№ 5» -> «номер пять»).

Использование:
    from textnorm import normalize
    normalize("В 1984 году было 25 человек.", "ru")
"""

import re
from decimal import Decimal, InvalidOperation

from num2words import num2words

SUPPORTED_LANGS = ("ru", "en")

# --- Русские числительные -----------------------------------------------------

# Суффикс порядкового числительного -> параметры num2words.
RU_ORDINAL_SUFFIXES = {
    "ыми": {"case": "i", "plural": True},
    "ой": {"case": "i", "gender": "f"},
    "ая": {"case": "n", "gender": "f"},
    "ое": {"case": "n", "gender": "n"},
    "ые": {"case": "n", "plural": True},
    "ых": {"case": "g", "plural": True},
    "ым": {"case": "i", "gender": "m"},
    "ом": {"case": "p", "gender": "m"},
    "го": {"case": "g", "gender": "m"},
    "му": {"case": "d", "gender": "m"},
    "й": {"case": "n", "gender": "m"},
    "я": {"case": "n", "gender": "f"},
    "е": {"case": "n", "gender": "n"},
    "ю": {"case": "a", "gender": "f"},
    "м": {"case": "p", "gender": "m"},
    "х": {"case": "g", "plural": True},
}

_RU_ORDINAL_RE = re.compile(
    r"(?<![\d])(\d+)-(ыми|ой|ая|ое|ые|ых|ым|ом|го|му|й|я|е|ю|м|х)\b"
)

# Падеж по слову «год» и по предлогу перед сокращением «г.».
RU_YEAR_FORMS = {
    "год": ("n", False, "год"),
    "года": ("g", False, "года"),
    "году": ("p", False, "году"),
    "годом": ("i", False, "годом"),
    "годе": ("p", False, "годе"),
    "годы": ("n", True, "годы"),
    "годах": ("p", True, "годах"),
    "годов": ("g", True, "годов"),
    "гг.": ("n", True, "годы"),
}

_RU_PREPOSITION_CASES = (
    (re.compile(r"\b(?:в|во)\s+$", re.IGNORECASE), "p", "году"),
    (re.compile(r"\b(?:к|ко)\s+$", re.IGNORECASE), "d", "году"),
    (re.compile(r"\b(?:с|со)\s+$", re.IGNORECASE), "g", "года"),
    (re.compile(r"\b(?:до|после|около|от)\s+$", re.IGNORECASE), "g", "года"),
)

_RU_YEAR_NOUN = r"год(?:ом|ах|ов|а|у|е|ы)?|гг?\."

# Диапазон с годом обрабатываем до одиночного года, иначе «1990–1995 годах»
# распадётся на число + отдельный год.
_RU_YEAR_RANGE_RE = re.compile(
    r"(?<![\d])(?P<start>\d{3,4})\s*[-–—]\s*(?P<end>\d{3,4})"
    r"(?:-[а-яё]{1,2})?\s*(?P<noun>" + _RU_YEAR_NOUN + r")",
    re.IGNORECASE,
)

_RU_YEAR_RE = re.compile(
    r"(?<![\d])(?P<num>\d{3,4})(?:-[а-яё]{1,2})?\s*"
    r"(?P<noun>" + _RU_YEAR_NOUN + r")",
    re.IGNORECASE,
)

# --- Английские числительные ---------------------------------------------------

_EN_YEAR_RE = re.compile(
    r"(?<![\d])(?P<num>\d{3,4})\s+(?P<noun>years?)\b", re.IGNORECASE
)
_EN_YEAR_PREP_RE = re.compile(
    r"\b(?P<prep>in|since|by|until|till|before|after)\s+"
    r"(?P<num>\d{3,4})\b(?!\s*(?:%|percent|per\s?cent))",
    re.IGNORECASE,
)
_EN_ORDINAL_RE = re.compile(r"(?<![\d])(\d+)(st|nd|rd|th)\b", re.IGNORECASE)

# --- Общие шаблоны -------------------------------------------------------------

_PERCENT_RE = re.compile(r"(?<![\w.,])(?P<num>\d+(?:[.,]\d+)?)\s*%")

_RU_RANGE_RE = re.compile(r"(?<![\d])(\d{3,4})\s*[-–—]\s*(\d{3,4})(?![\d])")
_EN_RANGE_RE = _RU_RANGE_RE

_NUMBER_SIGN_RE = re.compile(r"№\s*(?P<num>\d+(?:[.,]\d+)?)")

# Минус как знак числа: «-5», «(-5)», «= -5», но не дефис в «5-10»/«1984-1995».
_NEGATIVE_RE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9.,])-(?=\d)")

_NUM_BOUND_LEFT = r"(?<![A-Za-zА-Яа-яЁё0-9.,])"
_NUM_BOUND_RIGHT = r"(?![.,]\d)(?![A-Za-zА-Яа-яЁё0-9])"
_GROUPED_SPACES = r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+"

_RU_NUM_RE = re.compile(
    _NUM_BOUND_LEFT
    + r"(?P<num>" + _GROUPED_SPACES + r"(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    + _NUM_BOUND_RIGHT
)
_EN_NUM_RE = re.compile(
    _NUM_BOUND_LEFT
    + r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    + _NUM_BOUND_RIGHT
)

_PERCENT_FORMS = {
    "ru": ("процент", "процента", "процентов"),
    "en": ("percent", "percent", "percent"),
}


def _words(number, lang, **kwargs):
    """num2words с безопасным фолбэком: числа не должны ронять синтез."""
    try:
        return num2words(number, lang=lang, **kwargs)
    except (NotImplementedError, OverflowError, ValueError):
        return str(number)


def _ordinal(number, lang, case="n", gender="m", plural=False):
    if lang == "ru":
        return _words(number, lang, to="ordinal", case=case,
                      gender=gender, plural=plural)
    return _words(number, lang, to="ordinal")


def _year_words(number, lang):
    if lang == "ru":
        return _ordinal(number, lang, case="n", gender="m")
    return _words(number, lang, to="year")


def _plural_ru(number, one, few, many):
    n = abs(int(number))
    if n % 100 in (11, 12, 13, 14):
        return many
    if n % 10 == 1:
        return one
    if n % 10 in (2, 3, 4):
        return few
    return many


def _parse_number(token, lang):
    """Строку из текста -> Decimal. Разделители тысяч убираем по правилам языка."""
    t = token.replace("\u00a0", "").replace("\u202f", "").replace(" ", "")

    if lang == "ru":
        t = t.replace(",", ".")
    else:
        # В английском запятая — разделитель тысяч, точка — десятичная.
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", t):
            t = t.replace(",", "")
        else:
            t = t.replace(",", ".")

    try:
        return Decimal(t)
    except InvalidOperation:
        return None


def _number_words(value, lang):
    if value is None:
        return None
    if value == value.to_integral_value():
        return _words(int(value), lang)
    return _words(value, lang)


def _is_integer(value):
    return value is not None and value == value.to_integral_value()


# --- Отдельные проходы ---------------------------------------------------------

def _replace_number_sign(text, lang):
    word = "номер" if lang == "ru" else "number"

    def repl(match):
        value = _parse_number(match.group("num"), lang)
        words = _number_words(value, lang)
        return f"{word} {words}" if words else match.group(0)

    return _NUMBER_SIGN_RE.sub(repl, text)


def _replace_ru_ordinals(text):
    def repl(match):
        number = int(match.group(1))
        options = RU_ORDINAL_SUFFIXES[match.group(2)]
        return _ordinal(number, "ru", **options)

    return _RU_ORDINAL_RE.sub(repl, text)


def _replace_en_ordinals(text):
    return _EN_ORDINAL_RE.sub(
        lambda m: _ordinal(int(m.group(1)), "en"), text
    )


def _ru_year_case(prefix, noun):
    """Возвращает (падеж, число, слово) для года по слову «год» и предлогу."""
    if noun == "гг.":
        return "n", True, "годы"

    if noun == "г.":
        for pattern, prep_case, prep_noun in _RU_PREPOSITION_CASES:
            if pattern.search(prefix):
                return prep_case, False, prep_noun
        return "n", False, "год"

    case, plural, rendered_noun = RU_YEAR_FORMS[noun]

    # «году» одинаково выглядит в предложном («в … году») и дательном
    # («к … году») падежах — уточняем по предлогу.
    if noun == "году" and re.search(r"\b(?:к|ко)\s+$", prefix, re.IGNORECASE):
        case = "d"

    return case, plural, rendered_noun


def _replace_ru_years(text):
    def repl_range(match):
        start, end = int(match.group("start")), int(match.group("end"))
        case, plural, noun = _ru_year_case(
            text[: match.start()], match.group("noun").lower()
        )
        if plural:
            left = _ordinal(start, "ru", case=case, plural=True)
            right = _ordinal(end, "ru", case=case, plural=True)
            return f"{left}–{right} {noun}"
        left = _ordinal(start, "ru", case="g")
        right = _ordinal(end, "ru", case="a")
        return f"с {left} по {right} {noun}"

    def repl(match):
        number = int(match.group("num"))
        case, plural, noun = _ru_year_case(
            text[: match.start()], match.group("noun").lower()
        )
        return f"{_ordinal(number, 'ru', case=case, plural=plural)} {noun}"

    text = _RU_YEAR_RANGE_RE.sub(repl_range, text)
    return _RU_YEAR_RE.sub(repl, text)


def _replace_en_years(text):
    def repl_noun(match):
        return f"{_words(int(match.group('num')), 'en', to='year')} {match.group('noun')}"

    def repl_prep(match):
        return f"{match.group('prep')} {_words(int(match.group('num')), 'en', to='year')}"

    text = _EN_YEAR_RE.sub(repl_noun, text)
    return _EN_YEAR_PREP_RE.sub(repl_prep, text)


def _replace_percents(text, lang):
    one, few, many = _PERCENT_FORMS[lang]

    def repl(match):
        value = _parse_number(match.group("num"), lang)
        words = _number_words(value, lang)
        if words is None:
            return match.group(0)
        if lang == "ru" and _is_integer(value):
            form = _plural_ru(int(value), one, few, many)
        elif lang == "ru":
            form = few  # «три целых пять десятых процента»
        else:
            form = one
        return f"{words} {form}"

    return _PERCENT_RE.sub(repl, text)


def _replace_ranges(text, lang):
    pattern = _RU_RANGE_RE if lang == "ru" else _EN_RANGE_RE

    def repl_ru(match):
        start, end = int(match.group(1)), int(match.group(2))
        left = _ordinal(start, "ru", case="g")
        right = _ordinal(end, "ru", case="a")
        if re.search(r"\b(?:с|со)\s+$", text[: match.start()], re.IGNORECASE):
            return f"{left} по {right}"
        return f"с {left} по {right}"

    def repl_en(match):
        start, end = int(match.group(1)), int(match.group(2))
        left, right = _year_words(start, "en"), _year_words(end, "en")
        if re.search(r"\bfrom\s+$", text[: match.start()], re.IGNORECASE):
            return f"{left} to {right}"
        return f"from {left} to {right}"

    return pattern.sub(repl_ru if lang == "ru" else repl_en, text)


def _replace_numbers(text, lang):
    pattern = _RU_NUM_RE if lang == "ru" else _EN_NUM_RE

    def repl(match):
        value = _parse_number(match.group("num"), lang)
        words = _number_words(value, lang)
        return words if words is not None else match.group(0)

    return pattern.sub(repl, text)


def normalize(text, lang):
    """Заменяет числа и связанные символы на слова. Неизвестный язык — no-op."""
    if not text or lang not in SUPPORTED_LANGS:
        return text

    # Неразрывные/тонкие пробелы приводим к обычным только внутри чисел,
    # остальной текст не трогаем.
    result = text.replace("\u2212", "-")  # типографский минус

    result = _replace_number_sign(result, lang)
    result = _replace_ru_ordinals(result) if lang == "ru" else _replace_en_ordinals(result)
    result = _replace_ru_years(result) if lang == "ru" else _replace_en_years(result)
    result = _replace_percents(result, lang)
    result = _replace_ranges(result, lang)

    result = _NEGATIVE_RE.sub("минус " if lang == "ru" else "minus ", result)
    result = _replace_numbers(result, lang)

    return re.sub(r"[ \t]{2,}", " ", result).strip()

#!/usr/bin/env python3
"""Тесты нормализации чисел для Silero TTS.

Запуск из корня репозитория:

    tts/.venv/bin/python -m unittest discover -s tests -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tts"))

from textnorm import normalize  # noqa: E402


class RussianTest(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(normalize(text, "ru"), expected)

    def test_year_prepositional(self):
        self.check(
            "В 1984 году было 25 человек.",
            "В тысяча девятьсот восемьдесят четвёртом году было двадцать пять человек.",
        )

    def test_year_genitive_date(self):
        self.check(
            "5 мая 1984 года.",
            "пять мая тысяча девятьсот восемьдесят четвёртого года.",
        )

    def test_year_dative(self):
        self.check("к 2000 году", "к двухтысячному году")

    def test_year_abbreviation(self):
        self.check("в 1990 г.", "в тысяча девятьсот девяностом году")

    def test_year_plural(self):
        self.check("1990-х годах", "тысяча девятьсот девяностых годах")

    def test_year_range_plural(self):
        self.check(
            "В 1990–1995 годах.",
            "В тысяча девятьсот девяностых–тысяча девятьсот девяносто пятых годах.",
        )

    def test_decimal(self):
        self.check("3,14", "три целых четырнадцать сотых")

    def test_percent_agreement(self):
        self.check(
            "5% и 1% и 2%",
            "пять процентов и один процент и два процента",
        )

    def test_decimal_percent(self):
        self.check("3,5%", "три целых пять десятых процента")

    def test_number_sign(self):
        self.check("№ 5", "номер пять")

    def test_ordinal_suffixes(self):
        self.check(
            "1-й, 2-я, 3-е, 10-го",
            "первый, вторая, третье, десятого",
        )

    def test_range(self):
        self.check(
            "1990–1995",
            "с тысяча девятьсот девяностого по тысяча девятьсот девяносто пятого",
        )

    def test_thousands(self):
        self.check("1 000 000", "один миллион")

    def test_negative(self):
        self.check("-5", "минус пять")
        self.check("−7", "минус семь")

    def test_latin_word_untouched(self):
        self.check("IPv6", "IPv6")

    def test_unknown_lang_is_noop(self):
        self.assertEqual(normalize("5 яблок", "de"), "5 яблок")


class EnglishTest(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(normalize(text, "en"), expected)

    def test_year_with_preposition(self):
        self.check(
            "In 1984 there were 25 people.",
            "In nineteen eighty-four there were twenty-five people.",
        )

    def test_year_with_noun(self):
        self.check("1984 years", "nineteen eighty-four years")

    def test_decimal(self):
        self.check("3.14", "three point one four")

    def test_percent(self):
        self.check("5%", "five percent")

    def test_ordinals(self):
        self.check(
            "1st, 2nd, 3rd, 4th",
            "first, second, third, fourth",
        )

    def test_thousands(self):
        self.check("1,000,000", "one million")

    def test_range_with_existing_from(self):
        self.check(
            "From 1990–1995 he worked there.",
            "From nineteen ninety to nineteen ninety-five he worked there.",
        )

    def test_range_standalone(self):
        self.check(
            "1990–1995",
            "from nineteen ninety to nineteen ninety-five",
        )

    def test_negative(self):
        self.check("-5", "minus five")


if __name__ == "__main__":
    unittest.main()

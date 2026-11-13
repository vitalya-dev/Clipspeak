#!/usr/bin/env python3
"""Тесты очистки и разбивки текста в clipspeak.py.

Запуск из корня репозитория:

    .venv/bin/python -m unittest discover -s tests -v
"""

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent / "clipspeak.py"

spec = importlib.util.spec_from_file_location("clipspeak", MODULE_PATH)
clipspeak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clipspeak)

clean_text = clipspeak.clean_text
split_into_sentences = clipspeak.split_into_sentences


def sentences(text, language=None):
    cleaned = clean_text(text)
    lang = language or clipspeak.detect_language(cleaned)
    return split_into_sentences(cleaned, lang)


class HyphenTest(unittest.TestCase):
    def test_wrapped_word_is_joined(self):
        # "simpering" встречается в тексте ещё раз -> слово было разорвано вёрсткой
        text = "The simpering dog. A simper-\ning cat slept."
        self.assertEqual(clean_text(text), "The simpering dog. A simpering cat slept.")

    def test_real_hyphen_is_kept(self):
        self.assertEqual(
            clean_text("This is a well-\nknown fact."),
            "This is a well-known fact.",
        )

    def test_real_hyphen_kept_when_known_form_present(self):
        text = "It is well-known. Another well-\nknown case."
        self.assertEqual(clean_text(text), "It is well-known. Another well-known case.")

    def test_capitalized_second_part(self):
        self.assertEqual(clean_text("Анна-\nМария пришла."), "Анна-Мария пришла.")

    def test_uppercase_after_break_is_joined(self):
        text = "Это simper-\nIng вариант, а вот simpering."
        self.assertEqual(clean_text(text), "Это simpering вариант, а вот simpering.")


class FootnoteTest(unittest.TestCase):
    def test_footnote_number_is_removed(self):
        self.assertEqual(clean_text("This is a word.53 Next one."), "This is a word. Next one.")

    def test_decimal_is_kept(self):
        self.assertEqual(clean_text("Pi is 3.14 today."), "Pi is 3.14 today.")

    def test_reference_after_abbreviation_is_kept(self):
        text = "See Fig.5. Then eq.10 follows."
        self.assertEqual(clean_text(text), "See Fig.5. Then eq.10 follows.")
        self.assertEqual(sentences(text), ["See Fig.5.", "Then eq.10 follows."])

    def test_reference_with_trailing_dot_is_kept(self):
        self.assertEqual(clean_text("See Fig.5. And table 3.2."), "See Fig.5. And table 3.2.")

    def test_superscript_footnote_is_removed(self):
        self.assertEqual(clean_text("This is a word.\u00b9 And more."), "This is a word. And more.")

    def test_footnote_after_closing_quote_is_removed(self):
        self.assertEqual(
            clean_text('He said "hello."53 Next one.'),
            'He said "hello." Next one.',
        )


class HeaderTest(unittest.TestCase):
    def test_repeated_header_is_removed(self):
        text = "Page Title\nSome text here.\nPage Title\nMore text.\nPage Title\nFinal line."
        self.assertEqual(clean_text(text), "Some text here. More text. Final line.")

    def test_repeated_sentence_is_kept(self):
        text = "Никогда.\nНикогда.\nНикогда.\nИ снова никогда."
        self.assertEqual(clean_text(text), "Никогда. Никогда. Никогда. И снова никогда.")

    def test_page_numbers_are_removed(self):
        text = (
            "Page 3 of 10\nReal text one.\n- 12 -\nReal text two.\n"
            "Стр. 42\nReal text three.\niv\n"
        )
        self.assertEqual(clean_text(text), "Real text one. Real text two. Real text three.")


class MarkupTest(unittest.TestCase):
    def test_markdown_is_stripped(self):
        text = "## Heading\n\n**Bold** and [link](https://x.y) text.\n"
        self.assertEqual(clean_text(text), "Heading Bold and link text.")

    def test_code_block_is_dropped(self):
        text = "Text before.\n```python\nprint(1)\n```\nText after."
        self.assertEqual(clean_text(text), "Text before. Text after.")

    def test_table_rows_become_list(self):
        text = "Name | Age\n--- | ---\nJohn | 30"
        self.assertEqual(clean_text(text), "Name, Age John, 30")

    def test_emoji_are_removed(self):
        self.assertEqual(clean_text("Great job 🎉 done! Really?"), "Great job done! Really?")


class SplitTest(unittest.TestCase):
    def test_plain_sentences(self):
        self.assertEqual(
            sentences("One. Two! Three?"),
            ["One.", "Two!", "Three?"],
        )

    def test_garbage_is_not_a_sentence(self):
        cleaned = clean_text("...\n. . . . .\n| | |\n##\nReal sentence here.")
        self.assertEqual(split_into_sentences(cleaned, "en"), ["Real sentence here."])

    def test_leading_junk_is_stripped(self):
        self.assertEqual(sentences("## — Real sentence."), ["Real sentence."])

    def test_russian_abbreviations(self):
        text = "Это т.д. и т.п. А также др. вещи. 2024 г. был хорош."
        self.assertEqual(
            sentences(text, "ru"),
            ["Это т.д. и т.п. А также др. вещи.", "2024 г. был хорош."],
        )

    def test_long_sentence_is_chunked(self):
        text = " ".join(["word"] * 200)
        parts = sentences(text, "en")
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertLessEqual(len(part), clipspeak.MAX_SENTENCE_CHARS + 1)

    def test_dialogue_dashes_do_not_break_splitting(self):
        text = "— Привет! — сказал он. — Как дела?"
        self.assertEqual(
            sentences(text, "ru"),
            ["Привет!", "сказал он.", "Как дела?"],
        )


class RegressionTest(unittest.TestCase):
    def test_numbers_survive_cleaning(self):
        text = "В 1984 году было 25 человек. Это 5% населения. № 5 — важно!"
        self.assertEqual(clean_text(text), text)

    def test_dates_and_thousands_survive(self):
        text = "Дедлайн 15.03.2025, стоимость 1 000 000 руб."
        self.assertEqual(clean_text(text), text)

    def test_urls_survive(self):
        text = "See https://example.com/foo.bar or write to a.b@mail.ru."
        self.assertEqual(clean_text(text), text)


if __name__ == "__main__":
    unittest.main()

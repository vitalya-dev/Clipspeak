#!/home/vitalya/Projects/Clipspeak/.venv/bin/python

import subprocess
import sys
import os
import signal
import json
import urllib.request
import urllib.error
import threading
import queue
import tempfile
import re
import logging

# --- OPTIONAL DEPENDENCIES ---
# ftfy  - чинит mojibake/лигатуры/кавычки (частые проблемы текста из PDF)
# pysbd - умная разбивка на предложения (знает про Dr., e.g., Fig. 3 и т.п.)
# Если библиотеки не установлены, скрипт работает на запасном regex-режиме.
try:
    import ftfy
except ImportError:
    ftfy = None

try:
    import pysbd
except ImportError:
    pysbd = None

# --- SCRIPT CONFIGURATION ---
# TTS Settings
# URL/порты локальных TTS-серверов (см. systemd/silero-*.service.in)
TTS_URLS = {
	"en": "http://localhost:5001",
    "ru": "http://localhost:5002"
}
TTS_LENGTH_SCALE = 0.5  # Скорость речи (меньше = быстрее)

# Настройка встроенного Python-логгера
# Уровень INFO означает, что будут записываться обычные информационные сообщения и ошибки.
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Создаем сам объект логгера с именем нашего приложения
logger = logging.getLogger("clipspeak")


# Строки, состоящие только из номера страницы / декоративных символов.
# Ловим в том числе «Page 3 of 10», «- 12 -», «Стр. 42» и римские цифры.
PAGE_NUMBER_RE = re.compile(
	r'^[\s\-–—_·•|.]*'
	r'(?:'
	r'(?:pages?|p\.|стр\.?|с\.)\s*\d{1,4}(?:\s*(?:of|из|/)\s*\d{1,4})?'
	r'|\d{1,4}(?:\s*(?:of|из|/)\s*\d{1,4})?'
	r'|[ivxlc]{1,6}'
	r')'
	r'[\s\-–—_·•|.]*$',
	re.IGNORECASE,
)
# Суперскриптовые цифры (сноски в PDF)
SUPERSCRIPT_DIGITS = '\u00b9\u00b2\u00b3\u2070\u2074-\u2079'
# Мягкий перенос
SOFT_HYPHEN = '\u00ad'
# Невидимые юникод-пробелы
EXOTIC_SPACES = '\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000'

# Строка с таким концом — это предложение, а не колонтитул: даже если она
# повторяется в тексте, выкидывать её нельзя ("Никогда." x3 — это контент).
SENTENCE_END_RE = re.compile(r'[.!?…:;]\s*$')

# Эмодзи и декоративные символы: токенизатор Silero их всё равно вырезает,
# но из-за них в тексте остаются дыры и лишние паузы.
EMOJI_RE = re.compile('[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]')

# Аббревиатуры, после которых число — это ссылка ("Fig.5", "eq.10"),
# а не сноска. Сноски ("word.53") вырезаются.
FOOTNOTE_ABBREVS = frozenset({
	"fig", "figs", "eq", "eqs", "tab", "table", "ch", "chap", "sec",
	"p", "pp", "no", "vol", "art", "app", "ref", "refs",
	"гл", "рис", "табл", "стр", "с", "т", "п", "см", "д", "г",
	"руб", "коп", "млн", "млрд", "тыс",
})
FOOTNOTE_RE = re.compile(
	r'(?<![0-9])(?P<before>[A-Za-z\u0400-\u04FF]*)'
	r'(?P<punct>[.!?])(?P<quote>["\u201d\u2019\']?)(?P<num>\d{1,4})'
	r'(?=(?P<after>[\s,;:)\]]|$))'
)

# Перенос слова с дефисом на другую строку. Дефис считается «мягким»
# (слово было разорвано вёрсткой), если склеенная форма встречается в тексте
# ещё раз, и «настоящим» ("well-\nknown"), если чаще встречается дефисная.
# HYPHEN_JOIN используется, когда по тексту решить нельзя.
HYPHEN_JOIN = '-'
HYPHEN_SEP_RE = re.compile(
	r'(?<=[A-Za-z\u0400-\u04FF])[-\u2010\u2011][ \t]*\n[ \t]*(?=[A-Za-z\u0400-\u04FF])'
)
HYPHEN_BREAK_RE = re.compile(
	r'([A-Za-z\u0400-\u04FF]+)[-\u2010\u2011][ \t]*\n[ \t]*([A-Za-z\u0400-\u04FF]+)'
)

# Слишком длинное предложение режем на части: иначе стриминга нет
# (ждём весь WAV) и модель может «поплыть» на длинном тексте.
MAX_SENTENCE_CHARS = 300
LONG_SENTENCE_BREAKS = ',;:—–-'

# Буллеты и прочий мусор в начале предложения TTS читать не должен
# (тире в диалогах тоже снимаем: на речь оно не влияет).
LEADING_JUNK_RE = re.compile(r'^[\s#*|•‣▪◦·.…—–_=~\-]+')
# Предложение, где нет ни одной буквы/цифры, в TTS отправлять бессмысленно:
# "...", "| | |" и "##" дают HTTP 500 от сервера.
SPEAKABLE_RE = re.compile(r'[^\W_]')

# Сокращения, точку в которых pysbd (ru) принимает за конец предложения.
RU_ABBREVS_RE = re.compile(
	r'(?<![А-Яа-яЁёA-Za-z])'
	r'(?:т\s*\.\s*[дпечк]|т\s*\.\s*е|др|проч|см|ср|напр|им'
	r'|г|гг|стр|рис|табл|гл|п|пп|ул|д|кв|руб|коп|млн|млрд|тыс)'
	r'\s*\.',
	re.IGNORECASE,
)
ABBREV_PLACEHOLDER = '\ue000'

# Markdown, который не должен читаться вслух.
CODE_FENCE_RE = re.compile(
	r'^[ \t]*(?:```|~~~)[^\n]*\n.*?^[ \t]*(?:```|~~~)[^\n]*$',
	re.MULTILINE | re.DOTALL,
)
FENCE_LINE_RE = re.compile(r'^[ \t]*(?:```|~~~).*$', re.MULTILINE)
MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\([^)]*\)')
MD_LINK_RE = re.compile(r'\[([^\]]*)\]\([^)]*\)')
MD_HEADING_RE = re.compile(r'^[ \t]{0,3}#{1,6}[ \t]+', re.MULTILINE)
MD_HEADING_TAIL_RE = re.compile(r'[ \t]+#+[ \t]*$', re.MULTILINE)
MD_EMPHASIS_RE = re.compile(r'(\*\*|__)(?=\S)(.+?)(?<=\S)\1')
MD_ITALIC_RE = re.compile(r'(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])')
MD_CODE_RE = re.compile(r'`([^`\n]+)`')
TABLE_SEPARATOR_RE = re.compile(r'^[\s|:.\-–—]+$')


def _strip_markdown(text):
	"""Убирает markdown-разметку: блоки кода, ссылки, заголовки, эмфазис."""
	text = CODE_FENCE_RE.sub('', text)
	text = FENCE_LINE_RE.sub('', text)  # незакрытый блок кода
	text = MD_IMAGE_RE.sub(r'\1', text)
	text = MD_LINK_RE.sub(r'\1', text)
	text = MD_HEADING_RE.sub('', text)
	text = MD_HEADING_TAIL_RE.sub('', text)
	text = MD_EMPHASIS_RE.sub(r'\2', text)
	text = MD_ITALIC_RE.sub(r'\1', text)
	text = MD_CODE_RE.sub(r'\1', text)
	return text


def _scrub_table_row(line):
	"""Строку markdown-таблицы превращает в перечисление, разделитель — в пустую."""
	if '|' not in line:
		return line
	if TABLE_SEPARATOR_RE.match(line):
		return ''
	return re.sub(r'\s*\|\s*', ', ', line).strip(' ,')


def _join_wrapped_hyphens(text):
	"""
	Склеивает слова, разорванные переносом, и сохраняет настоящие дефисы.

	Для каждого разрыва сравниваем, какая форма встречается в тексте чаще:
	склеенная ("simpering") или дефисная ("well-known"). Если обе одинаково
	редки — оставляем HYPHEN_JOIN (по умолчанию дефис: лишний дефис модель
	прочитает как короткую паузу, а склеенное "wellknown" — как неверное слово).
	"""
	if not HYPHEN_SEP_RE.search(text):
		return text

	joined_probe = HYPHEN_SEP_RE.sub('', text).lower()
	hyphen_probe = HYPHEN_SEP_RE.sub('-', text).lower()

	def occurrences(probe, word):
		pattern = r'(?<!\w)' + re.escape(word) + r'(?!\w)'
		return len(re.findall(pattern, probe))

	def decide(match):
		left, right = match.group(1), match.group(2)

		joined = (left + right).lower()
		hyphenated = (left + '-' + right).lower()
		joined_count = occurrences(joined_probe, joined)
		hyphen_count = occurrences(hyphen_probe, hyphenated)

		if joined_count > hyphen_count:
			# слово было разорвано вёрсткой; вторая часть могла попасть
			# в начало строки с заглавной — вернём строчную
			return left + right[0].lower() + right[1:]
		if hyphen_count > joined_count:
			return left + '-' + right  # дефис настоящий ("well-known")
		return left + HYPHEN_JOIN + right

	return HYPHEN_BREAK_RE.sub(decide, text)


def _replace_footnote(match):
	"""Убирает номер сноски ("word.53" -> "word."), но не ссылку ("Fig.5")."""
	if match.group('before').lower() in FOOTNOTE_ABBREVS:
		return match.group(0)
	if (match.group('after') or '') in ('.', '!', '?'):
		return match.group(0)
	return match.group('before') + match.group('punct') + match.group('quote')


def clean_text(text):
	"""
	Очищает текст (в основном из PDF) перед разбивкой на предложения:
	1. ftfy: чинит mojibake ("Ã‰cole" -> "École"), лигатуры ("ﬁrst" -> "first"),
	   кривые кавычки и управляющие символы.
	2. Убирает мягкий перенос, невидимые пробелы и эмодзи.
	3. Снимает markdown-разметку и превращает строки таблиц в перечисления.
	4. Удаляет колонтитулы: строки-номера страниц и повторяющиеся короткие
	   строки без знака конца предложения.
	5. Склеивает разорванные переносом слова, сохраняя настоящие дефисы.
	6. Убирает сноски-цифры ("word.53" -> "word."), не задевая десятичные
	   числа ("3.14") и ссылки ("Fig.5", "eq.10").
	7. Схлопывает все пробелы и переводы строк.
	"""
	if not text:
		return text

	# 1. Починка кодировки и юникода (если ftfy установлен)
	if ftfy is not None:
		cleaned = ftfy.fix_text(text)
	else:
		cleaned = text

	# 2. Мягкий перенос -> пусто, невидимые пробелы -> обычный пробел
	cleaned = cleaned.replace(SOFT_HYPHEN, '')
	cleaned = re.sub('[' + EXOTIC_SPACES + ']', ' ', cleaned)

	# 2b. Удаляем прочие управляющие символы (кроме переводов строк)
	cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', cleaned)

	# 2c. Markdown и эмодзи
	cleaned = _strip_markdown(cleaned)
	cleaned = EMOJI_RE.sub('', cleaned)

	# 3. Работаем построчно: номера страниц, строки таблиц, колонтитулы
	lines = [ln.strip() for ln in cleaned.split('\n')]
	lines = [ln for ln in lines if ln and not PAGE_NUMBER_RE.match(ln)]
	lines = [_scrub_table_row(ln) for ln in lines]
	lines = [ln for ln in lines if ln]

	# Часто повторяющаяся короткая строка = колонтитул (верхний/нижний).
	# Строки, заканчивающиеся знаком препинания, — это контент, их не трогаем.
	counts = {}
	for ln in lines:
		counts[ln] = counts.get(ln, 0) + 1
	lines = [
		ln for ln in lines
		if not (
			len(ln) <= 60
			and counts[ln] >= 3
			and not SENTENCE_END_RE.search(ln)
		)
	]
	cleaned = '\n'.join(lines)

	# 4. Склейка слов, разорванных переносом (настоящие дефисы сохраняем)
	cleaned = _join_wrapped_hyphens(cleaned)

	# 5. Сноски: цифры, прилипшие к знаку конца предложения ("word.53" -> "word.")
	cleaned = FOOTNOTE_RE.sub(_replace_footnote, cleaned)
	#    Суперскриптовые сноски (¹ ² ³ ...) убираем целиком
	cleaned = re.sub('[' + SUPERSCRIPT_DIGITS + ']', '', cleaned)

	# 6. Схлопываем пробелы и переводы строк в одиночный пробел
	cleaned = re.sub(r'[ \t]+', ' ', cleaned)
	cleaned = re.sub(r'\s*\n\s*', ' ', cleaned)
	cleaned = re.sub(r'\s+', ' ', cleaned).strip()
	#    Пробел перед знаком препинания — типичный артефакт PDF и эмодзи
	cleaned = re.sub(r'\s+([.,!?;:%])', r'\1', cleaned)

	return cleaned


def _protect_abbreviations(text):
	"""
	Прячет точки в сокращениях ("т.д.", "стр.") от pysbd: иначе он режет
	"Это т.д. и т.п." на четыре «предложения». Возвращает текст и функцию
	восстановления.
	"""
	if not RU_ABBREVS_RE.search(text):
		return text, lambda s: s

	protected = RU_ABBREVS_RE.sub(
		lambda m: m.group(0).replace('.', ABBREV_PLACEHOLDER), text
	)
	return protected, lambda s: s.replace(ABBREV_PLACEHOLDER, '.')


def _split_long_sentence(sentence, limit=MAX_SENTENCE_CHARS):
	"""Режет слишком длинное предложение по запятой/тире/пробелу поближе к limit."""
	chunks = []
	rest = sentence
	while len(rest) > limit:
		head = rest[:limit]
		cut = max(head.rfind(ch) for ch in LONG_SENTENCE_BREAKS)
		if cut <= 0:
			cut = head.rfind(' ')
		if cut <= 0:
			next_space = rest.find(' ', limit)
			cut = next_space if next_space != -1 else len(rest)
		chunk = rest[:cut].strip()
		if chunk:
			chunks.append(chunk)
		rest = rest[cut:].strip()
	if rest:
		chunks.append(rest)
	return chunks or [sentence]


def split_into_sentences(text, language="en"):
	"""
	Разбивает переданный текст на список отдельных предложений.
	Если доступен pysbd - используем его (понимает сокращения: Dr., e.g., Fig. 3 и т.п.).
	Иначе - запасная разбивка регулярным выражением.

	Дополнительно: защищаем русские сокращения от pysbd, выбрасываем
	«предложения» без букв и цифр ("...", "| | |") и режем слишком длинные куски.
	"""
	# Если текст пустой, возвращаем пустой список
	if not text or not text.strip():
		return []

	protected_text, restore = _protect_abbreviations(text)

	parts = None

	if pysbd is not None:
		try:
			lang = language if language in ("en", "ru") else "en"
			segmenter = pysbd.Segmenter(language=lang)
			parts = segmenter.segment(protected_text)
		except Exception as e:
			logger.warning(f"Не удалось разбить текст через pysbd, используем regex: {e}")
			parts = None

	if parts is None:
		# Запасной вариант: разделение после . ! ? и пробельных символов
		parts = re.split(r'(?<=[.!?])\s+', protected_text.strip())

	cleaned_sentences = []
	for part in parts:
		part = restore(part)
		# Мусор в начале предложения ("##", "•", "...") TTS читать не должен
		part = LEADING_JUNK_RE.sub('', part).strip()
		# Мусор без единой буквы/цифры ("...", "| | |", "##") не озвучиваем
		if not part or not SPEAKABLE_RE.search(part):
			continue
		cleaned_sentences.extend(_split_long_sentence(part))

	return cleaned_sentences


def detect_language(text):
	"""
	Определяет язык текста по доле кириллических букв.
	Если кириллицы заметная часть - возвращаем 'ru', иначе 'en'.
	Так одна случайная русская цитата не переключит весь текст на русский голос.
	"""
	if not text:
		return "en" # По умолчанию считаем текст английским

	cyrillic = len(re.findall(r'[А-Яа-яЁё]', text))
	letters = len(re.findall(r'[A-Za-zА-Яа-яЁё]', text))

	if letters == 0:
		return "en"

	if cyrillic / letters > 0.2:
		return "ru"

	return "en"


def _is_python_process(pid):
	"""Проверяет, что PID — это интерпретатор Python, а не обёртка вроде timeout/bash."""
	try:
		exe = os.readlink(f"/proc/{pid}/exe")
	except OSError:
		return False
	return "python" in os.path.basename(exe).lower()


def kill_other_instances_of_self():
	"""
	Ищет и завершает другие запущенные копии этого скрипта
	и их группы процессов.

	pgrep -f ищет подстроку во всей командной строке, поэтому под шаблон
	попадают и обёртки (timeout, bash -c), в аргументах которых есть имя
	скрипта. Их трогать нельзя: убийство группы такой обёртки застрелит
	и сам текущий процесс. Поэтому оставляем только реальные python-процессы.
	"""
	current_pid = os.getpid()
	script_name = os.path.basename(__file__)
	print(f"Current instance: '{script_name}' (PID: {current_pid}). Checking for other instances...")

	try:
		pgrep_cmd = ["pgrep", "-f", script_name]
		result = subprocess.run(pgrep_cmd, capture_output=True, text=True, check=False)

		if result.returncode != 0:
			print("No other running instances found by pgrep.")
			return

		pids_to_check = result.stdout.strip().split('\n')
		terminated_count = 0
		for pid_str in pids_to_check:
			if not pid_str:
				continue

			try:
				pid = int(pid_str)
				if pid == current_pid:
					continue

				if not _is_python_process(pid):
					continue

				print(f"  Found previous instance (PID: {pid}). Terminating its process group...")
				pgid = os.getpgid(pid)
				os.killpg(pgid, signal.SIGTERM)
				print(f"    Sent SIGTERM to process group {pgid}.")
				terminated_count += 1
			except Exception as e:
				print(f"    Warning for PID '{pid_str}': Could not terminate. Reason: {type(e).__name__}", file=sys.stderr)

		if terminated_count > 0:
			print(f"Terminated {terminated_count} other instance(s).")

	except Exception as e:
		print(f"An unexpected error occurred while managing instances: {e}", file=sys.stderr)
	finally:
		print("-" * 20)


def handle_error(e, context_message, exit_code=1):
	"""
	Обрабатывает исключения, печатая отформатированное сообщение об ошибке, и завершает работу.
	"""
	print(f"\n--- SCRIPT ERROR ---", file=sys.stderr)
	print(f"Context: {context_message}", file=sys.stderr)
	print(f"Error Type: {type(e).__name__}", file=sys.stderr)
	print(f"Details: {e}", file=sys.stderr)
	print("----------------------\n", file=sys.stderr)
	sys.exit(exit_code)


def download_audio_worker(sentences, audio_queue, language):
	"""
	Фоновый поток: запрашивает аудио для каждого предложения у TTS-сервера,
	сохраняет во временные файлы и помещает пути к ним в очередь.
	Теперь учитывает выбранный язык для отправки запроса на нужный URL.
	"""
	# Определяем нужный URL на основе языка. 
	# Если язык по какой-то причине не найден, безопасно откатываемся на английский ('en')
	tts_url = TTS_URLS.get(language, TTS_URLS["en"])

	for text in sentences:
		if not text.strip():
			continue
			
		print(f"[Скачивание] Подготавливаем аудио ({language}) для: {text}")
		
		try:
			# Подготавливаем данные для TTS-сервера
			tts_payload = json.dumps({
				"text": text,
				"length_scale": TTS_LENGTH_SCALE
			}).encode('utf-8')
			
			req = urllib.request.Request(
				tts_url, 
				data=tts_payload, 
				headers={'Content-Type': 'application/json'}
			)
			
			# Создаем уникальный временный файл. 
			# delete=False нужно, чтобы файл не удалился сразу после закрытия
			temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
			temp_file_path = temp_file.name
			temp_file.close() # Закрываем, так как будем писать в него через urllib
			
			# Отправляем запрос и сохраняем результат
			with urllib.request.urlopen(req) as response, open(temp_file_path, 'wb') as out_file:
				out_file.write(response.read())
				
			# Кладем путь к готовому временному файлу в очередь
			audio_queue.put(temp_file_path)
			
		except Exception as e:
			print(f"[Ошибка скачивания] Не удалось получить аудио для '{text}': {e}", file=sys.stderr)
			
	# Когда все предложения обработаны, кладем None как сигнал завершения
	audio_queue.put(None)

def play_audio_worker(audio_queue):
	"""
	Фоновый поток: забирает пути к аудиофайлам из очереди,
	воспроизводит их и затем удаляет временные файлы.
	"""
	while True:
		# Ждем появления следующего файла в очереди (функция замирает и ждет)
		file_path = audio_queue.get()
		
		# Если получили None, значит генератор закончил работу и файлов больше не будет
		if file_path is None:
			break
			
		print(f"[Воспроизведение] Начинаем проигрывать файл...")
		
		try:
			# Воспроизводим аудио
			play_cmd_args = ["paplay", "--volume", "32768", file_path]
			subprocess.run(
				play_cmd_args, capture_output=True, text=True, check=True
			)
		except Exception as e:
			print(f"[Ошибка воспроизведения] Не удалось проиграть файл: {e}", file=sys.stderr)
		finally:
			# Обязательно удаляем временный файл после воспроизведения (или ошибки)
			try:
				os.remove(file_path)
			except OSError as e:
				print(f"[Ошибка очистки] Не удалось удалить временный файл {file_path}: {e}", file=sys.stderr)

if __name__ == "__main__":
	kill_other_instances_of_self()

	# --- Commands ---
	paste_cmd_args = ["wl-paste", "-p"]

	print("Запуск скрипта...")
	print("-" * 20)

	try:
		# 1. Получаем текст из буфера обмена
		print("Читаем буфер обмена (wl-paste)...")
		p1_result = subprocess.run(
			paste_cmd_args, capture_output=True, text=True, check=True
		)
		clipboard_content = p1_result.stdout
		
		if not clipboard_content.strip():
			print("Буфер обмена пуст, нечего озвучивать.")
			sys.exit(0)

		# Очищаем текст от сносок и разрывов строк
		clipboard_content = clean_text(clipboard_content)

		if ftfy is None or pysbd is None:
			missing = [name for name, mod in (("ftfy", ftfy), ("pysbd", pysbd)) if mod is None]
			print(f"[Предупреждение] Не установлены: {', '.join(missing)}. "
				  f"Качество очистки и разбивки будет ниже.", file=sys.stderr)
		
		# Отправляем очищенный текст в питоновский логгер
		logger.info(f"Cleaned text: {clipboard_content}")

		print("Копируем очищенный текст обратно в буфер...")
		try:
			subprocess.run(["wl-copy"], input=clipboard_content, text=True, check=True)
			print("Текст успешно закинут в буфер обмена.")
		except Exception as e:
			print(f"[Ошибка] Не удалось скопировать текст в буфер: {e}", file=sys.stderr)

		# 2. Определяем язык текста
		language = detect_language(clipboard_content)
		print(f"Определен язык: {language.upper()}")
		logger.info(f"Detected language: {language}")

		# 3. Разбиваем текст на предложения (с учётом языка)
		sentences = split_into_sentences(clipboard_content, language)
		print(f"Текст разбит на {len(sentences)} предложений(я). Начинаем потоковую обработку...")

		# 4. Настраиваем очередь и запускаем параллельную работу
		audio_queue = queue.Queue()
		
		# Запускаем скачивание аудио в отдельном фоновом потоке
		download_thread = threading.Thread(
			target=download_audio_worker, 
			args=(sentences, audio_queue, language) # Передаем язык сюда!
		)
		download_thread.start()
		
		# Запускаем воспроизведение прямо в главном потоке.
		# Эта функция заблокирует выполнение и будет ждать файлы, пока не получит сигнал None.
		play_audio_worker(audio_queue)
		
		# На всякий случай дожидаемся окончательного завершения фонового потока
		download_thread.join()

		print("\nОзвучивание успешно завершено.")

	except Exception as e:
		exit_code = e.returncode if isinstance(e, subprocess.CalledProcessError) else 1
		handle_error(e, "Произошла ошибка при выполнении скрипта", exit_code)

	print("-" * 20)
	print("Скрипт завершил работу.")

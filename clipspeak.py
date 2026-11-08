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
# Piper Settings
# Укажи здесь URL/порты, на которых запущены разные голоса Piper
PIPER_URLS = {
	"en": "http://localhost:5001",
    "ru": "http://localhost:5002"
}
PIPER_LENGTH_SCALE = 0.5  # Скорость речи (меньше = быстрее)

# Настройка встроенного Python-логгера
# Уровень INFO означает, что будут записываться обычные информационные сообщения и ошибки.
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
# Создаем сам объект логгера с именем нашего приложения
logger = logging.getLogger("clipspeak")


# Строки, состоящие только из номера страницы / декоративных символов
PAGE_NUMBER_RE = re.compile(r'^[\s\-–—_·•|.]*\d{1,4}[\s\-–—_·•|.]*$')
# Суперскриптовые цифры (сноски в PDF)
SUPERSCRIPT_DIGITS = '\u00b9\u00b2\u00b3\u2070\u2074-\u2079'
# Мягкий перенос
SOFT_HYPHEN = '\u00ad'
# Невидимые юникод-пробелы
EXOTIC_SPACES = '\u00a0\u1680\u2000-\u200b\u202f\u205f\u3000'
# Что ставить вместо переноса слова с дефисом: "" - склеить ("simper-\ning" -> "simpering"),
# " " - разделить ("сложно-\nсделать" -> "сложно сделать")
HYPHEN_JOIN = ''


def clean_text(text):
	"""
	Очищает текст (в основном из PDF) перед разбивкой на предложения:
	1. ftfy: чинит mojibake ("Ã‰cole" -> "École"), лигатуры ("ﬁrst" -> "first"),
	   кривые кавычки и управляющие символы.
	2. Убирает мягкий перенос и невидимые пробелы.
	3. Удаляет колонтитулы: строки-номера страниц и повторяющиеся короткие строки.
	4. Склеивает разорванные дефисом слова ("simper-\ning" -> "simpering"),
	   но только если после переноса идёт строчная буква (настоящие дефисы не трогаем).
	5. Убирает сноски-цифры ("word.53" -> "word."), не задевая десятичные числа ("3.14").
	6. Схлопывает все пробелы и переводы строк.
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

	# 3. Работаем построчно: выкидываем номера страниц и повторяющиеся колонтитулы
	lines = [ln.strip() for ln in cleaned.split('\n')]
	lines = [ln for ln in lines if ln and not PAGE_NUMBER_RE.match(ln)]

	# Часто повторяющаяся короткая строка = колонтитул (верхний/нижний)
	counts = {}
	for ln in lines:
		counts[ln] = counts.get(ln, 0) + 1
	lines = [ln for ln in lines if not (len(ln) <= 60 and counts[ln] >= 3)]
	cleaned = '\n'.join(lines)

	# 4. Склейка слов, разорванных дефисом в конце строки
	#    (дефис + перевод строки + строчная буква -> просто склеиваем)
	cleaned = re.sub(r'(?<=[A-Za-z\u0400-\u04FF])[-\u2010\u2011]\s*\n\s*(?=[a-z\u0430-\u044f])', HYPHEN_JOIN, cleaned)

	# 5. Сноски: цифры, прилипшие к знаку конца предложения ("word.53" -> "word.")
	#    Lookbehind не даёт сожрать десятичную точку: в "3.14" перед точкой стоит цифра.
	cleaned = re.sub(r'(?<![0-9])([.!?])[0-9]{1,4}(?=[\s,;:)\]\u201d\u2019"]|$)', r'\1', cleaned)
	#    Суперскриптовые сноски (¹ ² ³ ...) убираем целиком
	cleaned = re.sub('[' + SUPERSCRIPT_DIGITS + ']', '', cleaned)

	# 6. Схлопываем пробелы и переводы строк в одиночный пробел
	cleaned = re.sub(r'[ \t]+', ' ', cleaned)
	cleaned = re.sub(r'\s*\n\s*', ' ', cleaned)
	cleaned = re.sub(r'\s+', ' ', cleaned).strip()

	return cleaned


def split_into_sentences(text, language="en"):
	"""
	Разбивает переданный текст на список отдельных предложений.
	Если доступен pysbd - используем его (понимает сокращения: Dr., e.g., Fig. 3 и т.п.).
	Иначе - запасная разбивка регулярным выражением.
	"""
	# Если текст пустой, возвращаем пустой список
	if not text or not text.strip():
		return []

	parts = None

	if pysbd is not None:
		try:
			lang = language if language in ("en", "ru") else "en"
			segmenter = pysbd.Segmenter(language=lang)
			parts = segmenter.segment(text)
		except Exception as e:
			logger.warning(f"Не удалось разбить текст через pysbd, используем regex: {e}")
			parts = None

	if parts is None:
		# Запасной вариант: разделение после . ! ? и пробельных символов
		parts = re.split(r'(?<=[.!?])\s+', text.strip())

	# Очищаем каждое предложение от лишних пробелов по краям
	# и убираем пустые элементы, если они вдруг появились
	cleaned_sentences = [s.strip() for s in parts if s and s.strip()]

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


def kill_other_instances_of_self():
	"""
	Ищет и завершает другие запущенные копии этого скрипта
	и их группы процессов.
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
	Фоновый поток: запрашивает аудио для каждого предложения у Piper,
	сохраняет во временные файлы и помещает пути к ним в очередь.
	Теперь учитывает выбранный язык для отправки запроса на нужный URL.
	"""
	# Определяем нужный URL на основе языка. 
	# Если язык по какой-то причине не найден, безопасно откатываемся на английский ('en')
	piper_url = PIPER_URLS.get(language, PIPER_URLS["en"])

	for text in sentences:
		if not text.strip():
			continue
			
		print(f"[Скачивание] Подготавливаем аудио ({language}) для: {text}")
		
		try:
			# Подготавливаем данные для Piper
			piper_payload = json.dumps({
				"text": text,
				"length_scale": PIPER_LENGTH_SCALE
			}).encode('utf-8')
			
			req = urllib.request.Request(
				piper_url, 
				data=piper_payload, 
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

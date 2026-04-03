#!/usr/bin/env python3

import subprocess
import sys
import os
import shlex
import signal
import json
import urllib.request
import urllib.error
import threading
import queue
import tempfile
import re

# --- SCRIPT CONFIGURATION ---
# Piper Settings
PIPER_URL = "http://localhost:5001"
PIPER_LENGTH_SCALE = 0.5  # Скорость речи (меньше = быстрее)


def split_into_sentences(text):
	"""
	Разбивает переданный текст на список отдельных предложений.
	Использует регулярные выражения для поиска концов предложений (. ! ?).
	"""
	# Если текст пустой, возвращаем пустой список
	if not text or not text.strip():
		return []
		
	# Разделяем текст. (?<=[.!?]) означает "после знака препинания",
	# а \s+ означает "один или несколько пробелов/переносов строк".
	import re
	sentences = re.split(r'(?<=[.!?])\s+', text.strip())
	
	# Очищаем каждое предложение от лишних пробелов по краям 
	# и убираем пустые элементы, если они вдруг появились
	cleaned_sentences = [s.strip() for s in sentences if s.strip()]
	
	return cleaned_sentences


def kill_other_instances_of_self():
	"""
	Finds and terminates other running instances of the current script
	and their process groups (including children like paplay).
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
	Handles exceptions by printing a formatted error message and exiting.
	"""
	print(f"\n--- SCRIPT ERROR ---", file=sys.stderr)
	print(f"Context: {context_message}", file=sys.stderr)
	print(f"Error Type: {type(e).__name__}", file=sys.stderr)
	print(f"Details: {e}", file=sys.stderr)
	print("----------------------\n", file=sys.stderr)
	sys.exit(exit_code)


def download_audio_worker(sentences, audio_queue):
	"""
	Фоновый поток: запрашивает аудио для каждого предложения у Piper,
	сохраняет во временные файлы и помещает пути к ним в очередь.
	"""
	for text in sentences:
		if not text.strip():
			continue
			
		print(f"[Скачивание] Подготавливаем аудио для: {text}")
		
		try:
			# Подготавливаем данные для Piper
			piper_payload = json.dumps({
				"text": text,
				"length_scale": PIPER_LENGTH_SCALE
			}).encode('utf-8')
			
			req = urllib.request.Request(
				PIPER_URL, 
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

		# 2. Разбиваем текст на предложения
		sentences = split_into_sentences(clipboard_content)
		print(f"Текст разбит на {len(sentences)} предложений(я). Начинаем потоковую обработку...")

		# 3. Настраиваем очередь и запускаем параллельную работу
		audio_queue = queue.Queue()
		
		# Запускаем скачивание аудио в отдельном фоновом потоке
		download_thread = threading.Thread(
			target=download_audio_worker, 
			args=(sentences, audio_queue)
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
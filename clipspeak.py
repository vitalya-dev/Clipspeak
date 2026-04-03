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
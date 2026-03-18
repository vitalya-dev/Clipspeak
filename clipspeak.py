#!/usr/bin/env python3

import subprocess
import sys
import os
import shlex
import signal
import json
import urllib.request
import urllib.error
import re  # Добавлен модуль для работы с регулярными выражениями

# --- SCRIPT CONFIGURATION ---
# File Paths
output_wav_file = os.path.expanduser("~/1.wav")

# Piper Settings
PIPER_URL = "http://localhost:5001"
PIPER_LENGTH_SCALE = 0.5  # Скорость речи (меньше = быстрее)

# --- FUNCTIONS ---

def preprocess_text(text):
	"""
	Очищает текст от переносов слов, сносок (после точек) и форматирует пробелы.
	"""
	if not text:
		return text

	# 1. Убираем переносы слов: дефис, за которым следует перенос строки.
	text = re.sub(r'-\s*\n\s*', '', text)

	# 2. Заменяем все оставшиеся переносы строк и множественные пробелы на один пробел.
	text = re.sub(r'\s+', ' ', text)

	# 3. Убираем сноски: числа от 1 до 3 цифр, идущие после точки.
	text = re.sub(r'(\.)\s*\d{1,3}\s*', r'\1 ', text)

	# 4. Убираем лишние пробелы в начале и в конце текста.
	return text.strip()

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


# --- MAIN EXECUTION ---

if __name__ == "__main__":
	kill_other_instances_of_self()

	# --- Commands ---
	paste_cmd_args = ["wl-paste", "-p"]
	play_cmd_args = ["paplay", "--volume", "32768", output_wav_file] 

	# --- Execution ---
	active_commands = [
		" ".join(shlex.quote(arg) for arg in paste_cmd_args),
		"Piper API (POST)",
		" ".join(shlex.quote(arg) for arg in play_cmd_args)
	]

	print("Running sequentially: " + " -> ".join(active_commands))
	print("-" * 20)

	try:
		# 1. Run wl-paste
		print("Running wl-paste...")
		p1_result = subprocess.run(
			paste_cmd_args, capture_output=True, text=True, check=True
		)
		print("wl-paste finished.")
		
		# Обрабатываем полученный текст твоей функцией
		raw_clipboard_content = p1_result.stdout
		clipboard_content = preprocess_text(raw_clipboard_content)
		
		# Если после очистки текст оказался пустым, скрипт завершится
		if not clipboard_content:
			print("Clipboard is empty or contains only unreadable characters, nothing to speak.")
			sys.exit(0)

		# 2. Generate Audio with Piper
		print(f"Sending text to Piper server at {PIPER_URL} ...")
		piper_payload = json.dumps({
			"text": clipboard_content,
			"length_scale": PIPER_LENGTH_SCALE
		}).encode('utf-8')
		
		req = urllib.request.Request(
			PIPER_URL, 
			data=piper_payload, 
			headers={'Content-Type': 'application/json'}
		)
		
		with urllib.request.urlopen(req) as response, open(output_wav_file, 'wb') as out_file:
			out_file.write(response.read())
			
		print("Piper finished generating:", output_wav_file)

		# 3. Run paplay
		print("Running paplay...")
		subprocess.run(
			play_cmd_args, capture_output=True, text=True, check=True
		)
		print("paplay finished.")

		print("\nCommand sequence completed successfully.")

	except urllib.error.URLError as e:
		handle_error(e, f"Could not connect to Piper server. Is it running on {PIPER_URL}?")
	except Exception as e:
		exit_code = e.returncode if isinstance(e, subprocess.CalledProcessError) else 1
		handle_error(e, "An error occurred during the main command execution", exit_code)

	print("-" * 20)
	print("Script finished.")
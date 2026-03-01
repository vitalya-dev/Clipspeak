#!/usr/bin/env python3

import subprocess
import sys
import os
import shlex
import signal

import json            # Для создания JSON-структуры с текстом
import urllib.request  # Для отправки POST-запроса на сервер Piper


# --- SCRIPT CONFIGURATION ---
# File Paths
output_wav_file = os.path.expanduser("~/1.wav")

# Piper Settings
PIPER_URL = "http://localhost:5001"
PIPER_LENGTH_SCALE = 0.5  # Скорость чтения (из твоего примера с curl)

# --- FUNCTIONS ---

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
		# Find all processes matching the script name
		pgrep_cmd = ["pgrep", "-f", script_name]
		result = subprocess.run(pgrep_cmd, capture_output=True, text=True, check=False)

		if result.returncode != 0:
			# This handles both "no process found" (rc=1) and other pgrep errors
			print("No other running instances found by pgrep.")
			return

		# If we found processes, iterate and terminate them
		pids_to_check = result.stdout.strip().split('\n')
		terminated_count = 0
		for pid_str in pids_to_check:
			if not pid_str:
				continue

			# This try/except handles errors for each PID individually
			try:
				pid = int(pid_str)
				if pid == current_pid:
					continue  # Don't kill ourselves

				print(f"  Found previous instance (PID: {pid}). Terminating its process group...")
				pgid = os.getpgid(pid)
				os.killpg(pgid, signal.SIGTERM)
				print(f"    Sent SIGTERM to process group {pgid}.")
				terminated_count += 1
			except Exception as e:
				# A generic catch is fine here for warnings.
				print(f"    Warning for PID '{pid_str}': Could not terminate. Reason: {type(e).__name__}", file=sys.stderr)

		if terminated_count > 0:
			print(f"Terminated {terminated_count} other instance(s).")

	except Exception as e:
		# This outer catch handles bigger problems, e.g., 'pgrep' not found.
		print(f"An unexpected error occurred while managing instances: {e}", file=sys.stderr)
	finally:
		# This 'finally' ensures the separator line is always printed
		print("-" * 20)


# --- MAIN EXECUTION ---

if __name__ == "__main__":
	# Ensure only this instance of the script runs, terminating others and their children
	kill_other_instances_of_self()

	# --- Commands ---
	paste_cmd_args = ["wl-paste", "-p"]
	rhvoice_cmd_args = ["RHVoice-test", "-p", "clb", "-r", "200", "-o", output_wav_file]

	cmd_filter_base = ["sox", output_wav_file, temp_filtered_wav_file]
	cmd_filter_effects = []
	if HIGHPASS_FREQ:
		cmd_filter_effects.extend(["highpass", HIGHPASS_FREQ])
	if EQ_SETTINGS:
		cmd_filter_effects.extend(EQ_SETTINGS)
	if FADE_SETTINGS:
		cmd_filter_effects.extend(FADE_SETTINGS)
	if NORM_SETTINGS:
		cmd_filter_effects.extend(NORM_SETTINGS)

	cmd_filter_args = cmd_filter_base + cmd_filter_effects
	play_cmd_args = ["paplay", output_wav_file]

	# --- Execution ---
	active_commands = [
		" ".join(shlex.quote(arg) for arg in paste_cmd_args),
		" ".join(shlex.quote(arg) for arg in rhvoice_cmd_args)
	]
	if cmd_filter_effects:
		active_commands.append("Filter ({})".format(" ".join(shlex.quote(arg) for arg in cmd_filter_args)))
	else:
		active_commands.append("Filter (skipped)")
	active_commands.append(" ".join(shlex.quote(arg) for arg in play_cmd_args))

	print("Running sequentially: " + " -> ".join(active_commands))
	print("-" * 20)

	try:
		# 1. Run wl-paste
		print("Running wl-paste...")
		p1_result = subprocess.run(
			paste_cmd_args, capture_output=True, text=True, check=True
		)
		print("wl-paste finished.")
		clipboard_content = p1_result.stdout
		if not clipboard_content.strip():
			print("Clipboard is empty, nothing to speak.")
			sys.exit(0)

		# 2. Run RHVoice-test
		print("Running RHVoice-test...")
		subprocess.run(
			rhvoice_cmd_args, input=clipboard_content, capture_output=True, text=True, check=True
		)
		print("RHVoice-test finished generating:", output_wav_file)

		# 3. Run SoX filter chain
		if cmd_filter_effects:
			print(f"Running SoX filter chain...")
			p_filter_result = subprocess.run(
				cmd_filter_args, capture_output=True, text=True, check=True
			)
			print("SoX filtering finished.")

			os.replace(temp_filtered_wav_file, output_wav_file)
			print(f"Replaced {output_wav_file} with filtered version.")
		else:
			print("No SoX filters defined, skipping filtering.")

		# 4. Run paplay
		print("Running paplay...")
		subprocess.run(
			play_cmd_args, capture_output=True, text=True, check=True
		)
		print("paplay finished.")

		print("\nCommand sequence completed successfully.")

	except Exception as e:
		exit_code = e.returncode if isinstance(e, subprocess.CalledProcessError) else 1
		handle_error(e, "An error occurred during the main command execution", exit_code)

	finally:
		if os.path.exists(temp_filtered_wav_file):
			try:
				os.remove(temp_filtered_wav_file)
			except OSError as e:
				print(f"Warning: Could not remove temporary file {temp_filtered_wav_file}: {e}", file=sys.stderr)

	print("-" * 20)
	print("Script finished.")

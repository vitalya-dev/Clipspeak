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
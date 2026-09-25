"""Petits utilitaires partagés : tailles, noms de fichiers, limiteur de débit, mesure de vitesse."""

import os
import re
import threading
import time
from collections import deque
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "vdm"
STORE_PATH = APP_DIR / "queue.json"
DEFAULT_DEST = Path.home() / "Downloads" / "VDM"
DEFAULT_PORT = 9614

_UNITS = ["o", "Ko", "Mo", "Go", "To"]


def human_size(n):
    if n is None:
        return "?"
    n = float(n)
    for unit in _UNITS:
        if abs(n) < 1024 or unit == _UNITS[-1]:
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def human_time(seconds):
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_size(text):
    """'2M' -> 2097152, '500k' -> 512000, '0' ou None -> None (illimité)."""
    if not text:
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg]?)[bo]?\s*", str(text), re.I)
    if not m:
        raise ValueError(f"taille invalide : {text!r} (ex. 500K, 2M)")
    value = float(m.group(1)) * 1024 ** " kmg".index(m.group(2).lower() or " ")
    return int(value) or None


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_filename(name, default="telechargement"):
    name = _INVALID.sub("_", name or "").strip(" .")
    if not name:
        return default
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _RESERVED:
        stem = "_" + stem
    if len(stem) > 180:
        stem = stem[:180]
    return f"{stem}.{ext}" if ext else stem


class RateLimiter:
    """Seau à jetons partagé entre toutes les connexions (limite de vitesse globale)."""

    def __init__(self, rate=None):
        self.rate = rate
        self._lock = threading.Lock()
        self._allowance = 0.0
        self._last = time.monotonic()

    def consume(self, n):
        rate = self.rate
        if not rate:
            return
        with self._lock:
            now = time.monotonic()
            self._allowance = min(rate, self._allowance + (now - self._last) * rate)
            self._last = now
            self._allowance -= n
            wait = -self._allowance / rate if self._allowance < 0 else 0
        if wait > 0:
            time.sleep(wait)


class SpeedMeter:
    """Vitesse moyenne sur une fenêtre glissante de quelques secondes."""

    def __init__(self, window=3.0):
        self.window = window
        self.samples = deque()

    def update(self, total_bytes):
        now = time.monotonic()
        self.samples.append((now, total_bytes))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()

    @property
    def speed(self):
        if len(self.samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        return max(0.0, (b1 - b0) / (t1 - t0)) if t1 > t0 else 0.0

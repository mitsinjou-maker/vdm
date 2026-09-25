"""Calcul des plages horaires du planificateur et actions de fin de file."""

import subprocess
import sys
from datetime import datetime, time, timedelta

from .config import DAYS

# sans heure de fin, la file démarre si le daemon tourne dans l'heure qui suit l'heure prévue
START_GRACE = timedelta(hours=1)


def parse_hhmm(text):
    try:
        h, m = str(text).strip().split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        raise ValueError(f"heure invalide : {text!r} (format HH:MM)") from None


def parse_days(text):
    """'lun,mer,ven' ou 'tous' -> [0, 2, 4] / []."""
    if not text or text.strip().lower() in ("tous", "all", "*"):
        return []
    days = []
    for part in text.lower().replace(" ", "").split(","):
        if part[:3] not in DAYS:
            raise ValueError(f"jour inconnu : {part!r} (lun, mar, mer, jeu, ven, sam, dim)")
        days.append(DAYS.index(part[:3]))
    return sorted(set(days))


def _day_ok(schedule, d):
    return not schedule.days or d.weekday() in schedule.days


def current_window(schedule, now=None):
    """Date de début de la plage en cours (identifiant de la plage), ou None."""
    now = now or datetime.now()
    start, t, today = parse_hhmm(schedule.start), now.time(), now.date()
    if schedule.stop:
        stop = parse_hhmm(schedule.stop)
        if start < stop:
            return today if _day_ok(schedule, today) and start <= t < stop else None
        if t >= start and _day_ok(schedule, today):
            return today
        yesterday = today - timedelta(days=1)  # plage à cheval sur minuit
        return yesterday if t < stop and _day_ok(schedule, yesterday) else None
    begin = datetime.combine(today, start)
    return today if _day_ok(schedule, today) and begin <= now < begin + START_GRACE else None


def next_start(schedule, now=None):
    now = now or datetime.now()
    start = parse_hhmm(schedule.start)
    for i in range(8):
        day = now.date() + timedelta(days=i)
        candidate = datetime.combine(day, start)
        if candidate > now and _day_ok(schedule, day):
            return candidate
    return None


def describe(schedule):
    days = "tous les jours" if not schedule.days else ", ".join(DAYS[d] for d in schedule.days)
    stop = f"–{schedule.stop}" if schedule.stop else " (jusqu'à la fin de la file)"
    return f"{schedule.start}{stop}, {days}"


def run_after_action(action):
    """Exécutée quand la file planifiée est terminée."""
    if sys.platform != "win32" or action == "rien":
        return
    if action == "arret":  # délai d'une minute, annulable avec « shutdown /a »
        subprocess.Popen(["shutdown", "/s", "/t", "60", "/c",
                          "VDM : téléchargements planifiés terminés. Annuler : shutdown /a"])
    elif action == "veille":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])

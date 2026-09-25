"""Moteur HTTP multi-connexions à segmentation dynamique (le principe d'IDM).

Le fichier est découpé en segments téléchargés en parallèle via des requêtes Range.
Quand une connexion termine son segment, elle ne reste pas inactive : elle coupe en
deux le segment restant le plus long et en récupère la seconde moitié. Toutes les
connexions travaillent ainsi jusqu'au dernier octet.

L'état (segments + position) est sauvegardé dans `<fichier>.vdm` à côté du `.part`,
ce qui permet de reprendre après une pause, un Ctrl+C ou une coupure réseau.
"""

import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

import requests
from requests.adapters import HTTPAdapter

from .util import sanitize_filename

CHUNK = 64 * 1024
MIN_SPLIT = 512 * 1024          # on ne coupe pas un segment s'il reste moins de 2 × MIN_SPLIT
TIMEOUT = (15, 30)              # (connexion, lecture)
MAX_FAILURES = 15               # échecs consécutifs sans aucun octet reçu avant abandon
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")


class DownloadError(Exception):
    pass


class HttpStatusError(DownloadError):
    def __init__(self, status):
        super().__init__(f"le serveur a répondu HTTP {status}")
        self.status = status


class Paused(Exception):
    """Levée quand un téléchargement est interrompu volontairement (pause, arrêt)."""


def make_session(headers=None, pool=8):
    s = requests.Session()
    # identity : on veut les octets bruts, sinon les calculs de Range sont faux
    s.headers.update({"User-Agent": DEFAULT_UA, "Accept": "*/*", "Accept-Encoding": "identity"})
    s.headers.update({k: v for k, v in (headers or {}).items() if v})
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=pool + 2)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


class ProbeResult:
    def __init__(self, url, size, resumable, filename, content_type):
        self.url = url
        self.size = size
        self.resumable = resumable
        self.filename = filename
        self.content_type = content_type


def probe(url, session):
    """Demande 1 octet pour connaître taille, support du Range, nom et type."""
    r = session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=TIMEOUT)
    if r.status_code >= 400:  # certains serveurs refusent l'en-tête Range
        r.close()
        r = session.get(url, stream=True, timeout=TIMEOUT)
    with r:
        if r.status_code >= 400:
            raise HttpStatusError(r.status_code)
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        size, resumable = None, False
        if r.status_code == 206:
            m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
            if m:
                size, resumable = int(m.group(1)), True
        else:
            cl = r.headers.get("Content-Length", "")
            size = int(cl) if cl.isdigit() else None
        return ProbeResult(r.url, size, resumable, _filename_from(r, ctype), ctype)


def _filename_from(response, ctype):
    cd = response.headers.get("Content-Disposition", "")
    name = None
    m = re.search(r"filename\*\s*=\s*([\w-]*)'[^']*'([^;]+)", cd, re.I)
    if m:
        try:
            name = unquote(m.group(2).strip(), encoding=m.group(1) or "utf-8")
        except LookupError:
            name = unquote(m.group(2).strip())
    if not name:
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
        name = m.group(1).strip() if m else unquote(PurePosixPath(urlparse(response.url).path).name)
    name = sanitize_filename(name)
    if not Path(name).suffix and ctype:
        ext = mimetypes.guess_extension(ctype)
        if ext:
            name += ext
    return name


def choose_path(dest_dir, name):
    """Chemin final libre ; réutilise un téléchargement interrompu du même nom (reprise)."""
    path = Path(dest_dir) / name
    if _state_path(path).exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 1
    while path.exists() or _part_path(path).exists():
        path = path.with_name(f"{stem} ({i}){suffix}")
        i += 1
    return path


def _part_path(path):
    return path.with_name(path.name + ".part")


def _state_path(path):
    return path.with_name(path.name + ".vdm")


class Segment:
    __slots__ = ("start", "end", "pos", "active", "lock")

    def __init__(self, start, end, pos=None):
        self.start = start            # inclus
        self.end = end                # exclu ; peut rétrécir quand une autre connexion « vole » la fin
        self.pos = start if pos is None else pos
        self.active = False
        self.lock = threading.Lock()


class HttpDownload:
    def __init__(self, url, dest_dir, *, path=None, filename=None, connections=8,
                 headers=None, limiter=None, stop_event=None, probe_result=None):
        self.url = url
        self.dest_dir = Path(dest_dir)
        self.path = Path(path) if path else None
        self.filename = filename
        self.connections = max(1, connections)
        self.limiter = limiter
        self.stop_event = stop_event or threading.Event()
        self.info = probe_result
        self.session = make_session(headers, self.connections)
        self.size = None
        self.resumable = False
        self.segments = []
        self.active_connections = 0
        self.title = None
        self._seg_lock = threading.Lock()
        self._stat_lock = threading.Lock()
        self._single_done = 0
        self._failures = 0
        self._alive = 0
        self._error = None

    # --- état observable ----------------------------------------------------

    @property
    def downloaded(self):
        if self.segments:
            return sum(s.pos - s.start for s in list(self.segments))
        return self._single_done

    def segment_map(self, width):
        """Fraction téléchargée de chaque case d'une barre de `width` cases."""
        if not self.size or not self.segments:
            done = (self._single_done / self.size) if self.size else 0
            return [1.0 if (i + 1) / width <= done else 0.0 for i in range(width)]
        cells = [0] * width
        step = self.size / width
        for s in list(self.segments):
            a, b = s.start, s.pos
            if b <= a:
                continue
            for i in range(int(a / step), min(width, int((b - 1) / step) + 1)):
                lo, hi = i * step, (i + 1) * step
                cells[i] += max(0, min(b, hi) - max(a, lo))
        return [min(1.0, c / step) for c in cells]

    def pause(self):
        self.stop_event.set()

    # --- exécution ----------------------------------------------------------

    def run(self):
        info = self.info or probe(self.url, self.session)
        self.url = info.url  # URL finale après redirections
        self.size = info.size
        self.resumable = info.resumable and bool(info.size)
        if self.path is None:
            self.path = choose_path(self.dest_dir, sanitize_filename(self.filename or info.filename))
        self.title = self.path.name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = _part_path(self.path)
        state = _state_path(self.path)

        if self.resumable:
            self._run_segmented(part, state)
        else:
            state.unlink(missing_ok=True)
            self._run_single(part)

        if self.stop_event.is_set():
            raise Paused()
        if self._error:
            raise self._error
        os.replace(part, self.path)
        state.unlink(missing_ok=True)
        return self.path

    def _run_segmented(self, part, state):
        self.segments = self._load_state(part, state) or self._initial_segments()
        if not part.exists() or part.stat().st_size != self.size:
            with open(part, "wb") as f:
                f.truncate(self.size)  # préallocation

        threads = [threading.Thread(target=self._worker, args=(part,), daemon=True)
                   for _ in range(self.connections)]
        for t in threads:
            t.start()
            time.sleep(0.05)  # ouverture progressive des connexions
        last_save = time.monotonic()
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
            if time.monotonic() - last_save >= 1:
                self._save_state(state)
                last_save = time.monotonic()
        self._save_state(state)
        if not self.stop_event.is_set() and not self._error and any(s.pos < s.end for s in self.segments):
            self._error = DownloadError("téléchargement incomplet")

    def _initial_segments(self):
        n = max(1, min(self.connections, self.size // MIN_SPLIT))
        bounds = [self.size * i // n for i in range(n + 1)]
        return [Segment(bounds[i], bounds[i + 1]) for i in range(n)]

    def _load_state(self, part, state):
        try:
            data = json.loads(state.read_text(encoding="utf-8"))
            if data["size"] != self.size or not part.exists() or part.stat().st_size != self.size:
                return None
            return [Segment(a, b, p) for a, b, p in data["segments"]]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_state(self, state):
        with self._seg_lock:
            segs = [[s.start, s.end, s.pos] for s in self.segments]
        tmp = state.with_name(state.name + ".tmp")
        tmp.write_text(json.dumps({"url": self.url, "size": self.size, "segments": segs}), encoding="utf-8")
        os.replace(tmp, state)

    def _next_segment(self):
        with self._seg_lock:
            for s in self.segments:
                if not s.active and s.pos < s.end:
                    s.active = True
                    return s
            # plus rien de libre : on coupe en deux le segment actif le plus long
            busy = [s for s in self.segments if s.active]
            if not busy:
                return None
            best = max(busy, key=lambda s: s.end - s.pos)
            with best.lock:
                remaining = best.end - best.pos
                if remaining < 2 * MIN_SPLIT:
                    return None
                mid = best.pos + remaining // 2
                new = Segment(mid, best.end)
                best.end = mid
            new.active = True
            self.segments.append(new)
            return new

    def _worker(self, part):
        with self._stat_lock:
            self._alive += 1
        try:
            while not self.stop_event.is_set() and self._error is None:
                seg = self._next_segment()
                if seg is None:
                    return
                try:
                    self._fetch(seg, part)
                except Exception as e:  # réseau, disque, HTTP…
                    with self._seg_lock:
                        seg.active = False
                    if self.stop_event.is_set():
                        return
                    status = getattr(e, "status", None)
                    with self._stat_lock:
                        self._failures += 1
                        failures, alive = self._failures, self._alive
                    if failures >= MAX_FAILURES:
                        self._error = e if isinstance(e, DownloadError) else DownloadError(str(e))
                        return
                    if status in (429, 503) and alive > 1:
                        return  # le serveur limite les connexions : on en ferme une
                    self.stop_event.wait(min(30, 2 ** min(failures, 5)))
        finally:
            with self._stat_lock:
                self._alive -= 1

    def _fetch(self, seg, part):
        with seg.lock:
            start, end = seg.pos, seg.end
        if start >= end:
            with self._seg_lock:
                seg.active = False
            return
        with self.session.get(self.url, headers={"Range": f"bytes={start}-{end - 1}"},
                              stream=True, timeout=TIMEOUT) as r:
            if r.status_code != 206:
                raise HttpStatusError(r.status_code)
            with self._stat_lock:
                self.active_connections += 1
            try:
                with open(part, "r+b") as f:
                    f.seek(start)
                    for chunk in r.iter_content(CHUNK):
                        if self.stop_event.is_set():
                            break
                        if self.limiter:
                            self.limiter.consume(len(chunk))
                        with seg.lock:
                            n = min(len(chunk), seg.end - seg.pos)
                            if n > 0:
                                f.write(chunk if n == len(chunk) else chunk[:n])
                                seg.pos += n
                            done = seg.pos >= seg.end
                        if n:
                            self._failures = 0
                        if done:
                            break
            finally:
                with self._stat_lock:
                    self.active_connections -= 1
        with self._seg_lock:
            seg.active = False
        if not self.stop_event.is_set() and seg.pos < seg.end:
            raise DownloadError("connexion interrompue")

    def _run_single(self, part):
        """Serveur sans Range : une seule connexion, pas de reprise possible."""
        self._single_done = 0
        with self.session.get(self.url, stream=True, timeout=TIMEOUT) as r:
            if r.status_code >= 400:
                raise HttpStatusError(r.status_code)
            self.active_connections = 1
            try:
                with open(part, "wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        if self.stop_event.is_set():
                            return
                        if self.limiter:
                            self.limiter.consume(len(chunk))
                        f.write(chunk)
                        self._single_done += len(chunk)
            finally:
                self.active_connections = 0
        if self.size and self._single_done < self.size:
            self._error = DownloadError("connexion interrompue avant la fin")

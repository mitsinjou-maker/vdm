"""File d'attente persistante : ordonnancement, catégories, planificateur, playlists."""

import json
import os
import re
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import scheduler, ytdl
from .config import Config, category_for
from .engine import DownloadError, HttpDownload, Paused, make_session, probe
from .util import STORE_PATH, RateLimiter, SpeedMeter, sanitize_filename

QUEUED, DOWNLOADING, PAUSED, DONE, ERROR = "en attente", "en cours", "en pause", "terminé", "erreur"
MAIN, SCHEDULED = "principale", "planifiée"

_MANIFEST = re.compile(r"\.(m3u8|mpd)(\?|$)", re.I)


@dataclass
class Job:
    id: int
    url: str
    dest: str
    kind: str = "auto"            # auto | http | ytdl
    quality: str = "best"
    connections: int = 8
    headers: dict = field(default_factory=dict)
    title: str | None = None
    status: str = QUEUED
    path: str | None = None
    size: int | None = None
    downloaded: int = 0
    error: str | None = None
    added: float = field(default_factory=time.time)
    finished: float | None = None
    queue: str = MAIN
    category: str | None = None   # fixée au premier démarrage
    auto_dir: bool = False        # True : le sous-dossier de la catégorie sera ajouté à `dest`
    playlist: bool = False        # URL vidéo + playlist : prendre toute la playlist
    prefix: str = ""              # « 03 - » pour les éléments de playlist
    audio_lang: str = ""          # langue de la piste audio (sites vidéo) ; "" = d'origine
    note: str | None = None       # remarque non bloquante (ex. langue audio indisponible)
    subs: str = ""                # sous-titres : "fr,en" ; "" = aucun
    subs_auto: bool = False
    subs_embed: bool = False
    speed: float = 0.0            # champs volatils, mis à jour pendant le téléchargement
    active: int = 0

    @property
    def name(self):
        if self.path:
            return Path(self.path).name
        return self.title or self.url

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def detect_kind(url, headers=None):
    """'http' pour un fichier direct, 'ytdl' pour une page ou un flux. Renvoie aussi la sonde."""
    if _MANIFEST.search(url):
        return "ytdl", None
    try:
        info = probe(url, make_session(headers, 1))
    except Exception:
        if ytdl.available():
            return "ytdl", None
        raise
    ctype = info.content_type
    if ctype.startswith("text/") or "xhtml" in ctype or "mpegurl" in ctype or "dash+xml" in ctype:
        if not ytdl.available():
            raise DownloadError("c'est une page web : installez yt-dlp pour la gérer")
        return "ytdl", None
    return "http", info


def prepare(url, kind, headers, quality):
    """Choisit le moteur et la catégorie. Renvoie (kind, sonde, catégorie)."""
    info = None
    if kind == "auto":
        kind, info = detect_kind(url, headers)
    elif kind == "http":
        info = probe(url, make_session(headers, 1))
    if info:
        category = category_for(info.filename, info.content_type)
    else:
        category = "Musique" if quality == "audio" else "Vidéos"
    return kind, info, category


def build_downloader(kind, url, dest, *, quality="best", connections=8, headers=None,
                     limiter=None, stop_event=None, path=None, probe_result=None,
                     playlist=False, prefix="", audio_lang="", subs="", subs_auto=False, subs_embed=False,
                     title_hint=None):
    if kind == "ytdl":
        return ytdl.YtdlDownload(url, dest, quality=quality, connections=connections, headers=headers,
                                 rate_limit=limiter.rate if limiter else None, stop_event=stop_event,
                                 playlist=playlist, prefix=prefix, audio_lang=audio_lang,
                                 subs=subs, subs_auto=subs_auto, subs_embed=subs_embed,
                                 title_hint=title_hint)
    return HttpDownload(url, dest, path=path, connections=connections, headers=headers,
                        limiter=limiter, stop_event=stop_event, probe_result=probe_result)


class Manager:
    def __init__(self, config=None, store_path=STORE_PATH):
        self.config = config or Config.load()
        self.store_path = Path(store_path)
        self.limiter = RateLimiter(self.config.speed_limit)
        self.jobs: dict[int, Job] = {}
        self.lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._downloaders = {}
        self._stops = {}
        self._threads = {}
        self._meters = {}
        self._to_remove = {}      # id -> supprimer aussi les fichiers ?
        self._requeue = set()     # arrêtés par le planificateur : repassent « en attente »
        self._sub_tasks = set()   # téléchargements terminés dont on récupère des sous-titres
        self._stopping = False
        self._loop_thread = None
        # planificateur
        self.sched_active = False
        self._win = None          # plage horaire en cours
        self._win_done = None     # plage dont la file a été terminée
        self._win_ids = set()     # téléchargements lancés pendant la plage
        self.load()

    # raccourcis vers la configuration
    @property
    def dest(self):
        return Path(self.config.dest)

    @property
    def max_parallel(self):
        return self.config.parallel

    # --- persistance --------------------------------------------------------

    def load(self):
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data.get("jobs", []):
            job = Job.from_dict(d)
            if job.status == DOWNLOADING:  # arrêt brutal : on reprendra
                job.status = QUEUED
            job.speed, job.active = 0.0, 0
            self.jobs[job.id] = job

    def save(self):
        with self.lock:
            data = {"jobs": [j.to_dict() for j in self.jobs.values()]}
        text = json.dumps(data, ensure_ascii=False, indent=1)
        with self._save_lock:  # plusieurs threads enregistrent : un seul à la fois
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store_path.with_name(self.store_path.name + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            for attempt in range(5):  # Windows : fichier parfois verrouillé (antivirus, indexation)
                try:
                    os.replace(tmp, self.store_path)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.1)

    def apply_config(self, config):
        """Applique (et enregistre) de nouveaux réglages, y compris pendant les téléchargements."""
        with self.lock:
            self.config = config
            self.limiter.rate = config.speed_limit
        config.save()

    # --- commandes ----------------------------------------------------------

    def add(self, url, *, dest=None, kind="auto", quality="best", headers=None, title=None,
            connections=None, queue=MAIN, playlist=False, prefix="", category=None, audio_lang=None,
            subs=None, subs_auto=None, subs_embed=None):
        """Les options laissées à None prennent la valeur par défaut des réglages."""
        cfg = self.config
        audio_lang = ytdl.check_lang(cfg.audio_lang if audio_lang is None else audio_lang)
        subs = ytdl.parse_subs(cfg.subs if subs is None else subs)
        subs_auto = cfg.subs_auto if subs_auto is None else bool(subs_auto)
        subs_embed = cfg.subs_embed if subs_embed is None else bool(subs_embed)
        with self.lock:
            job = Job(id=max(self.jobs, default=0) + 1, url=url, dest=str(dest or self.dest),
                      kind=kind, quality=quality, headers=headers or {}, title=title,
                      connections=connections or self.config.connections,
                      queue=SCHEDULED if queue == SCHEDULED else MAIN, playlist=bool(playlist),
                      prefix=prefix, category=category, auto_dir=dest is None, audio_lang=audio_lang,
                      subs=subs, subs_auto=subs_auto, subs_embed=subs_embed)
            self.jobs[job.id] = job
        self.save()
        return job

    def _select(self, ref):
        with self.lock:
            if ref == "all":
                return list(self.jobs.values())
            job = self.jobs.get(int(ref))
            if job is None:
                raise KeyError(f"aucun téléchargement n°{ref}")
            return [job]

    def pause(self, ref):
        for job in self._select(ref):
            with self.lock:
                if job.status == QUEUED:
                    job.status = PAUSED
                elif job.status == DOWNLOADING:
                    self._stops[job.id].set()
        self.save()

    def resume(self, ref):
        for job in self._select(ref):
            with self.lock:
                if job.status in (PAUSED, ERROR):
                    job.status, job.error = QUEUED, None
        self.save()

    def set_queue(self, ref, queue):
        """Déplace vers la file principale ou planifiée (hors de la plage : il s'arrête)."""
        for job in self._select(ref):
            with self.lock:
                if job.status != DONE:
                    job.queue = SCHEDULED if queue == SCHEDULED else MAIN
        self.save()

    def remove(self, ref, delete_files=False):
        for job in self._select(ref):
            with self.lock:
                if job.status == DOWNLOADING:
                    self._to_remove[job.id] = delete_files
                    self._stops[job.id].set()
                    continue
                self.jobs.pop(job.id, None)
            if delete_files:
                _delete_files(job)
        self.save()

    def clean(self):
        with self.lock:
            for jid in [j.id for j in self.jobs.values() if j.status == DONE]:
                del self.jobs[jid]
        self.save()

    def fetch_subs(self, ref, langs, auto=False, embed=False, wait=False):
        """Récupère des sous-titres pour une vidéo déjà téléchargée (en arrière-plan par défaut)."""
        job = self._select(ref)[0]
        if job.status != DONE or not job.path:
            raise ValueError("le téléchargement doit être terminé")
        if job.kind != "ytdl":
            raise ValueError("sous-titres possibles uniquement pour les vidéos de sites (YouTube…)")
        if not Path(job.path).exists():
            raise ValueError(f"fichier introuvable : {job.path}")
        codes = ytdl.parse_subs(langs)
        if not codes:
            raise ValueError("aucune langue de sous-titres choisie")
        with self.lock:
            if job.id in self._sub_tasks:
                raise ValueError("sous-titres déjà en cours pour ce téléchargement")
            self._sub_tasks.add(job.id)
            job.note = f"⏳ sous-titres en cours : {codes}"
        args = (job, codes, bool(auto), bool(embed))
        if wait:
            self._fetch_subs(*args)
        else:
            threading.Thread(target=self._fetch_subs, args=args, daemon=True).start()
        return job

    def _fetch_subs(self, job, codes, auto, embed):
        try:
            added, failed, embedded = ytdl.fetch_subtitles(job.url, job.path, codes, auto, embed, job.headers)
            parts = []
            if added:
                parts.append(f"sous-titres ajoutés : {', '.join(added)}" + (" (intégrés)" if embedded else " (.srt)"))
                job.subs = ytdl.parse_subs(",".join([job.subs, *added]))
                job.subs_embed = job.subs_embed or embedded
            if failed:
                parts.append("sans sous-titres " + ", ".join(f"{c} ({why})" for c, why in failed.items()))
            job.note = " ; ".join(parts) or None
        except Exception as e:  # noqa: BLE001 — affiché dans la liste
            job.note = f"sous-titres : {e}"
        finally:
            with self.lock:
                self._sub_tasks.discard(job.id)
            self.save()

    def set_speed_limit(self, rate):
        self.config.speed_limit = rate
        self.apply_config(self.config)

    def set_schedule(self, schedule):
        self.config.schedule = schedule
        with self.lock:
            self._win_done = None   # une nouvelle programmation repart de zéro
        self.apply_config(self.config)

    def downloader(self, jid):
        """Objet de téléchargement en cours (pour afficher la carte des segments)."""
        with self.lock:
            return self._downloaders.get(jid)

    def schedule_status(self):
        s = self.config.schedule
        if not s.enabled:
            return "planificateur désactivé"
        if self.sched_active:
            until = f" jusqu'à {s.stop}" if s.stop else " jusqu'à la fin de la file"
            return f"file planifiée active{until}"
        nxt = scheduler.next_start(s)
        return f"file planifiée : prochain départ {nxt:%a %d/%m %H:%M}" if nxt else "planificateur : aucun jour choisi"

    # --- boucle d'ordonnancement --------------------------------------------

    def start(self):
        self._loop_thread = threading.Thread(target=self._loop, daemon=True)
        self._loop_thread.start()

    def stop(self, timeout=10):
        """Met en pause les téléchargements en cours ; ils reprendront au prochain démarrage."""
        self._stopping = True
        with self.lock:
            for ev in self._stops.values():
                ev.set()
            threads = list(self._threads.values())
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.1, deadline - time.monotonic()))
        self.save()

    def _loop(self):
        last_save = 0.0
        while not self._stopping:
            try:
                last_save = self._tick(last_save)
            except Exception:  # une erreur ponctuelle ne doit pas arrêter l'ordonnanceur
                traceback.print_exc()
            time.sleep(0.5)

    def _tick(self, last_save):
        """Un tour d'ordonnancement ; renvoie l'heure de la dernière sauvegarde."""
        with self.lock:
            after_action = self._schedule_tick()
            running = sum(1 for j in self.jobs.values() if j.status == DOWNLOADING)
            for job in sorted(self.jobs.values(), key=lambda j: j.id):
                if running >= self.max_parallel:
                    break
                if job.status == QUEUED and (job.queue == MAIN or self.sched_active):
                    self._launch(job)
                    running += 1
            self._refresh()
        if after_action:
            self.save()
            scheduler.run_after_action(after_action)
        if time.monotonic() - last_save > 2:
            self.save()
            last_save = time.monotonic()
        return last_save

    def _schedule_tick(self):
        """Met à jour self.sched_active ; renvoie l'action de fin de file à exécuter, le cas échéant."""
        s = self.config.schedule
        win = None
        if s.enabled:
            try:
                win = scheduler.current_window(s)
            except ValueError:
                win = None
            # sans heure de fin, une plage commencée continue jusqu'à vider la file
            if win is None and not s.stop and self._win is not None and self._win_ids \
                    and self._win != self._win_done:
                win = self._win
        active = win is not None and win != self._win_done
        if active and win != self._win:
            self._win, self._win_ids = win, set()

        action = None
        if active and self._win_ids:
            sched = [j for j in self.jobs.values() if j.queue == SCHEDULED]
            if not any(j.status in (QUEUED, DOWNLOADING) for j in sched):
                self._win_done, active = win, False
                launched = [self.jobs[i] for i in self._win_ids if i in self.jobs]
                if launched and all(j.status in (DONE, ERROR) for j in launched):
                    action = s.after
        if not active:
            for job in self.jobs.values():
                if job.queue == SCHEDULED and job.status == DOWNLOADING and job.id in self._stops:
                    self._requeue.add(job.id)
                    self._stops[job.id].set()
        self.sched_active = active
        return action if action and action != "rien" else None

    def _refresh(self):
        for jid, dl in list(self._downloaders.items()):
            job = self.jobs.get(jid)
            if job is None:
                continue
            job.downloaded = dl.downloaded
            job.size = dl.size or job.size
            job.active = dl.active_connections
            if getattr(dl, "path", None) and job.kind == "http":
                job.path = str(dl.path)
            if dl.title and not job.title:
                job.title = dl.title
            job.note = getattr(dl, "note", None) or job.note
            meter = self._meters.setdefault(jid, SpeedMeter())
            meter.update(job.downloaded)
            job.speed = meter.speed

    def _launch(self, job):
        job.status, job.error = DOWNLOADING, None
        if job.queue == SCHEDULED:
            self._win_ids.add(job.id)
        self._stops[job.id] = threading.Event()
        self._meters[job.id] = SpeedMeter()
        t = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._threads[job.id] = t
        t.start()

    def _run(self, job):
        stop = self._stops[job.id]
        try:
            probe_result = None
            if job.kind == "auto" or job.category is None:
                job.kind, probe_result, category = prepare(job.url, job.kind, job.headers, job.quality)
                job.category = job.category or category
            if job.auto_dir:  # dossier figé au premier démarrage, pour que la reprise le retrouve
                if self.config.categorize:
                    job.dest = str(Path(job.dest) / job.category)
                job.auto_dir = False
            if stop.is_set():
                raise Paused()
            dl = build_downloader(job.kind, job.url, job.dest, quality=job.quality,
                                  connections=job.connections, headers=job.headers,
                                  limiter=self.limiter, stop_event=stop, path=job.path,
                                  probe_result=probe_result, playlist=job.playlist, prefix=job.prefix,
                                  audio_lang=job.audio_lang, subs=job.subs, subs_auto=job.subs_auto,
                                  subs_embed=job.subs_embed, title_hint=job.title)
            with self.lock:
                self._downloaders[job.id] = dl
            path = dl.run()
            with self.lock:
                self._refresh()
                job.status, job.path, job.finished = DONE, str(path), time.time()
                job.downloaded = job.size = max(job.downloaded, job.size or 0) or None
        except ytdl.PlaylistFound as p:
            self._expand_playlist(job, p)
        except Paused:
            job.status = QUEUED if self._stopping or job.id in self._requeue else PAUSED
        except Exception as e:
            job.status, job.error = ERROR, str(e)[:300] or e.__class__.__name__
        finally:
            with self.lock:
                if job.id in self._downloaders:
                    self._refresh()
                    del self._downloaders[job.id]
                job.speed, job.active = 0.0, 0
                self._requeue.discard(job.id)
                self._stops.pop(job.id, None)
                self._threads.pop(job.id, None)
                self._meters.pop(job.id, None)
                if job.id in self._to_remove:
                    delete = self._to_remove.pop(job.id)
                    self.jobs.pop(job.id, None)
                    if delete:
                        _delete_files(job)
            if not self._stopping:
                self.save()

    def _expand_playlist(self, job, found):
        """Remplace la playlist par un téléchargement par élément, dans un sous-dossier."""
        folder = Path(job.dest) / sanitize_filename(found.title or "Playlist")
        width = len(str(max((i for i, _, _ in found.entries), default=1)))
        with self.lock:
            for index, url, title in found.entries:
                self.add(url, dest=folder, kind="ytdl", quality=job.quality, headers=job.headers,
                         title=title, connections=job.connections, queue=job.queue,
                         prefix=f"{index:0{width}d} - ", category=job.category, audio_lang=job.audio_lang,
                         subs=job.subs, subs_auto=job.subs_auto, subs_embed=job.subs_embed)
            self.jobs.pop(job.id, None)
            self._win_ids.discard(job.id)


def _delete_files(job):
    if not job.path:
        return
    p = Path(job.path)
    for f in (p, p.with_name(p.name + ".part"), p.with_name(p.name + ".vdm")):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass

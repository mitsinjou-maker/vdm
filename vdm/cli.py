"""Interface en ligne de commande de VDM."""

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import requests
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from dataclasses import asdict

from . import __version__, scheduler, ytdl
from .config import AFTER_ACTIONS, Config, Schedule
from .engine import DownloadError, HttpDownload, Paused
from .manager import (DONE, DOWNLOADING, ERROR, MAIN, PAUSED, QUEUED, SCHEDULED, Manager,
                      build_downloader, prepare)
from .server import serve
from .util import DEFAULT_PORT, RateLimiter, SpeedMeter, human_size, human_time, parse_size

console = Console()

STATUS_STYLE = {QUEUED: "yellow", DOWNLOADING: "cyan", PAUSED: "magenta", DONE: "green", ERROR: "red"}


# --- client de l'API du daemon ------------------------------------------------

class DaemonError(Exception):
    pass


def api(port, method, path, data=None):
    """Appelle le daemon ; renvoie None s'il ne tourne pas."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except ValueError:
            msg = None
        raise DaemonError(msg or f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError):
        return None


# --- affichage ------------------------------------------------------------------

def _progress_cell(size, done, status):
    grid = Table.grid(padding=(0, 1))
    if status == DONE:
        bar, pct = ProgressBar(total=1, completed=1, width=18), "100%"
    elif size:
        bar, pct = ProgressBar(total=size, completed=done, width=18), f"{done / size:4.0%}"
    else:
        bar = ProgressBar(total=None, width=18, pulse=status == DOWNLOADING)
        pct = ""
    grid.add_row(bar, pct)
    return grid


def jobs_table(jobs):
    t = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False)
    t.add_column("#", justify="right", style="dim", width=3)
    t.add_column("Fichier", ratio=3, min_width=12, overflow="ellipsis", no_wrap=True)
    t.add_column("Progression", width=24, no_wrap=True)
    t.add_column("Taille", justify="right", no_wrap=True)
    t.add_column("Vitesse", justify="right", no_wrap=True)
    t.add_column("Reste", justify="right", no_wrap=True)
    t.add_column("Cx", justify="right", width=3)
    t.add_column("Catégorie", style="dim", no_wrap=True)
    t.add_column("État", ratio=2, min_width=10, overflow="ellipsis", no_wrap=True)
    for j in jobs:
        size, done, speed, status = j["size"], j["downloaded"], j["speed"], j["status"]
        running = status == DOWNLOADING
        eta = (size - done) / speed if running and size and speed else None
        state = Text(status, style=STATUS_STYLE.get(status, ""))
        if j.get("queue") == SCHEDULED and status != DONE:
            state.append(" ⏰", style="yellow")
        if status == ERROR and j.get("error"):
            state.append(f" — {j['error']}", style="dim red")
        elif j.get("note"):
            state.append(f" — {j['note']}", style="dim yellow")
        t.add_row(
            str(j["id"]), j["name"], _progress_cell(size, done, status),
            human_size(size) if size else human_size(done) if done else "?",
            f"{human_size(speed)}/s" if running else "",
            human_time(eta) if running else "",
            str(j["active"] or "") if running else "",
            j.get("category") or "",
            state)
    return t


def segment_bar(dl, width=60):
    cells = dl.segment_map(width)
    text = Text()
    for c in cells:
        if c >= 0.999:
            text.append("█", style="green")
        elif c > 0:
            text.append("▒", style="cyan")
        else:
            text.append("·", style="dim")
    return text


# --- commandes ------------------------------------------------------------------

def _headers(a):
    return {"Referer": a.referer} if getattr(a, "referer", None) else {}


def cmd_get(a):
    headers = _headers(a)
    cfg = Config.load()
    stop = threading.Event()
    limiter = RateLimiter(parse_size(a.limit) if a.limit else cfg.speed_limit)
    with console.status("Analyse de l'URL…"):
        kind, info, category = prepare(a.url, a.kind, headers, a.quality)
    dest = Path(a.output) if a.output else Path(cfg.dest) / (category if cfg.categorize else "")
    dl = build_downloader(kind, a.url, dest, quality=a.quality, connections=a.connections or cfg.connections,
                          headers=headers, limiter=limiter, stop_event=stop, probe_result=info,
                          playlist=a.playlist,
                          audio_lang=ytdl.check_lang(cfg.audio_lang if a.audio_lang is None else a.audio_lang),
                          subs=cfg.subs if a.subs is None else a.subs,
                          subs_auto=cfg.subs_auto if a.auto_subs is None else a.auto_subs,
                          subs_embed=cfg.subs_embed if a.embed_subs is None else a.embed_subs)
    result = {}

    def target():
        try:
            result["path"] = dl.run()
        except BaseException as e:  # noqa: BLE001 — remonté au thread principal
            result["error"] = e

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    meter = SpeedMeter()

    def render():
        done, size = dl.downloaded, dl.size
        meter.update(done)
        speed = meter.speed
        eta = (size - done) / speed if size and speed else None
        stats = Text.assemble(
            (f"{human_size(done)} / {human_size(size)}", "bold"),
            f"  ·  {human_size(speed)}/s  ·  reste {human_time(eta)}  ·  ",
            (f"{dl.active_connections} connexion(s)", "cyan"))
        parts = [Text(dl.title or a.url, style="bold", overflow="ellipsis", no_wrap=True),
                 _progress_cell(size, done, DOWNLOADING), stats]
        if isinstance(dl, HttpDownload) and dl.segments:
            parts += [segment_bar(dl), Text(f"{len(dl.segments)} segments", style="dim")]
        return Group(*parts)

    try:
        with Live(render(), console=console, refresh_per_second=4, transient=True) as live:
            while worker.is_alive():
                worker.join(0.25)
                live.update(render())
    except KeyboardInterrupt:
        stop.set()
        with console.status("Mise en pause…"):
            worker.join(20)

    err = result.get("error")
    if isinstance(err, ytdl.PlaylistFound):
        console.print(f"C'est une playlist ({len(err.entries)} éléments). Ajoutez-la à la file pour tout "
                      f"télécharger :\n  vdm add --playlist \"{escape(a.url)}\"")
        return 1
    if stop.is_set() or isinstance(err, Paused):
        console.print("[magenta]En pause.[/] Relancez la même commande pour reprendre.")
        return 130
    if err:
        console.print(f"[red]Échec :[/] {escape(str(err))}")
        return 1
    if getattr(dl, "note", None):
        console.print(f"[yellow]Remarque :[/] {escape(dl.note)}")
    console.print(f"[green]Terminé :[/] {escape(str(result['path']))}")
    return 0


def cmd_daemon(a):
    if api(a.port, "GET", "/api/ping"):
        console.print(f"[yellow]Un daemon VDM tourne déjà sur le port {a.port}.[/]")
        return 1
    m = Manager(_session_config(a))
    try:
        httpd = serve(m, a.port)
    except OSError as e:
        console.print(f"[red]Impossible d'écouter sur le port {a.port} :[/] {escape(str(e))}")
        return 1
    m.start()

    def render():
        with m.lock:
            jobs = [dict(j.to_dict(), name=j.name) for j in m.jobs.values()]
        active = [j for j in jobs if j["status"] != DONE]
        shown = active[-25:] + [j for j in jobs if j["status"] == DONE][-5:]
        shown.sort(key=lambda j: j["id"])
        total_speed = sum(j["speed"] for j in jobs)
        running = sum(1 for j in jobs if j["status"] == DOWNLOADING)
        limit = f"{human_size(m.limiter.rate)}/s" if m.limiter.rate else "aucune"
        header = Text.assemble(
            ("VDM ", "bold cyan"), f"· 127.0.0.1:{a.port} · ",
            (f"{running}/{m.max_parallel} actif(s)", "bold"), f" · {human_size(total_speed)}/s",
            f" · limite {limit} · dossier {m.dest}")
        footer = Text.assemble((f"⏰ {m.schedule_status()}\n", "yellow"), (
            "Ctrl+C pour quitter : les téléchargements reprendront au prochain lancement.", "dim"))
        body = jobs_table(shown) if shown else Text("\nFile vide. Ajoutez une URL avec `vdm add URL` "
                                                     "ou via l'extension.\n", style="dim")
        return Group(header, body, footer)

    try:
        with Live(render(), console=console, refresh_per_second=2) as live:
            while True:
                time.sleep(0.5)
                live.update(render())
    except KeyboardInterrupt:
        pass
    finally:
        with console.status("Arrêt : mise en pause des téléchargements…"):
            m.stop()
            httpd.shutdown()
    return 0


def _session_config(a):
    """Réglages enregistrés, surchargés pour cette session par les options de la ligne de commande."""
    cfg = Config.load()
    if a.output:
        cfg.dest = str(Path(a.output).resolve())
    if a.parallel:
        cfg.parallel = a.parallel
    if a.connections:
        cfg.connections = a.connections
    if a.limit:
        cfg.speed_limit = parse_size(a.limit)
    return cfg


def cmd_add(a):
    offline = None
    dest = str(Path(a.output).resolve()) if a.output else None
    for url in a.urls:
        queue = SCHEDULED if a.scheduled else MAIN
        payload = {"url": url, "dest": dest, "kind": a.kind, "quality": a.quality,
                   "headers": _headers(a), "connections": a.connections, "queue": queue,
                   "playlist": a.playlist, "audio_lang": a.audio_lang, "subs": a.subs,
                   "subs_auto": a.auto_subs, "subs_embed": a.embed_subs}
        r = api(a.port, "POST", "/api/add", payload)
        if r is not None:
            console.print(f"[green]Ajouté[/] n°{r['id']} : {escape(url)}")
            continue
        offline = offline or Manager()
        job = offline.add(url, dest=dest, kind=a.kind, quality=a.quality, headers=_headers(a),
                          connections=a.connections, queue=queue, playlist=a.playlist,
                          audio_lang=a.audio_lang, subs=a.subs, subs_auto=a.auto_subs,
                          subs_embed=a.embed_subs)
        console.print(f"[yellow]Ajouté hors ligne[/] n°{job.id} : {escape(url)} "
                      f"[dim](démarrera au lancement de `vdm daemon`)[/]")
    return 0


def cmd_list(a):
    r = api(a.port, "GET", "/api/jobs")
    if r is None:
        m = Manager()
        jobs = [dict(j.to_dict(), name=j.name) for j in m.jobs.values()]
        console.print("[dim]Daemon arrêté — état enregistré :[/]")
    else:
        jobs = r["jobs"]
    if not jobs:
        console.print("File vide.")
        return 0
    console.print(jobs_table(sorted(jobs, key=lambda j: j["id"])))
    return 0


def _control(a, action):
    code = 0
    for ref in a.refs:
        try:
            data = {"delete": getattr(a, "delete", False), "queue": getattr(a, "queue", None)}
            if api(a.port, "POST", f"/api/jobs/{ref}/{action}", data) is None:
                m = Manager()
                if action == "remove":
                    m.remove(ref, delete_files=data["delete"])
                elif action == "move":
                    m.set_queue(ref, data["queue"])
                else:
                    getattr(m, action)(ref)
            console.print(f"[green]OK[/] {action} {ref}")
        except (DaemonError, KeyError, ValueError) as e:
            console.print(f"[red]{escape(ref)} :[/] {escape(str(e.args[0] if e.args else e))}")
            code = 1
    return code


def cmd_clean(a):
    if api(a.port, "POST", "/api/clean", {}) is None:
        Manager().clean()
    console.print("Téléchargements terminés retirés de la liste.")
    return 0


def cmd_limit(a):
    r = api(a.port, "POST", "/api/limit", {"rate": parse_size(a.rate)})
    if r is None:
        console.print("[yellow]Le daemon ne tourne pas.[/] Utilisez `vdm daemon --limit …`.")
        return 1
    console.print(f"Limite de vitesse : {human_size(r['limit']) + '/s' if r['limit'] else 'aucune'}")
    return 0


def cmd_schedule(a):
    r = api(a.port, "GET", "/api/schedule")
    online = r is not None
    sched = Schedule(**{k: v for k, v in (r or asdict(Config.load().schedule)).items()
                        if k in Schedule.__dataclass_fields__})
    if a.action in ("on", "off"):
        sched.enabled = a.action == "on"
    if a.start:
        scheduler.parse_hhmm(a.start)
        sched.start = a.start
    if a.stop:
        sched.stop = None if a.stop.lower() in ("aucune", "none", "-") else a.stop
        if sched.stop:
            scheduler.parse_hhmm(sched.stop)
    if a.days:
        sched.days = scheduler.parse_days(a.days)
    if a.after:
        sched.after = a.after
    changed = a.action != "show" or any((a.start, a.stop, a.days, a.after))
    if changed:
        if online:
            api(a.port, "POST", "/api/schedule", asdict(sched))
        else:
            cfg = Config.load()
            cfg.schedule = sched
            cfg.save()
    state = "[green]activé[/]" if sched.enabled else "[dim]désactivé[/]"
    console.print(f"Planificateur {state} : {scheduler.describe(sched)} ; "
                  f"à la fin : {AFTER_ACTIONS[sched.after].lower()}")
    if sched.enabled and (nxt := scheduler.next_start(sched)):
        console.print(f"Prochain départ de la file planifiée : {nxt:%d/%m à %H:%M}")
    if not online and changed:
        console.print("[dim]Le daemon ne tourne pas : réglage enregistré pour son prochain lancement.[/]")
    return 0


def _find_job(port, ref):
    r = api(port, "GET", "/api/jobs")
    jobs = r["jobs"] if r is not None else [j.to_dict() for j in Manager().jobs.values()]
    job = next((j for j in jobs if str(j["id"]) == str(ref)), None)
    if job is None:
        raise ValueError(f"aucun téléchargement n°{ref}")
    return job


def cmd_subs(a):
    job = _find_job(a.port, a.ref)
    if a.list or not a.langs:
        a.url = job["url"]
        return cmd_langs(a, only_subs=True)
    r = api(a.port, "POST", f"/api/jobs/{a.ref}/subs", {"subs": a.langs, "auto": a.auto, "embed": a.embed})
    if r is not None:
        console.print(f"Sous-titres demandés pour n°{a.ref} : suivez l'avancement avec `vdm list`.")
        return 0
    m = Manager()  # daemon arrêté : on le fait tout de suite
    with console.status("Téléchargement des sous-titres…"):
        done = m.fetch_subs(a.ref, a.langs, a.auto, a.embed, wait=True)
    console.print(escape(done.note or "terminé"))
    return 0


def cmd_langs(a, only_subs=False):
    with console.status("Recherche des pistes audio et des sous-titres…"):
        tracks = ytdl.media_tracks(a.url, _headers(a))
    audio, subs, auto = tracks["audio"], tracks["subs"], tracks["auto"]

    def table(title, rows, option):
        t = Table(title=title, title_justify="left", title_style="bold", box=box.SIMPLE_HEAD, pad_edge=False)
        t.add_column(f"Code ({option})", style="bold cyan")
        t.add_column("Langue")
        for code, label in rows:
            t.add_row(code, label)
        console.print(t)

    if only_subs:
        pass
    elif audio:
        table("Pistes audio", audio, "-a")
    else:
        console.print("Pistes audio : une seule langue.")
    if subs:
        table("Sous-titres", subs, "--subs")
    else:
        console.print("Sous-titres : aucun sous-titre écrit par l'auteur.")
    if auto:
        common = ", ".join(code for code, _ in auto if code in ("fr", "en", "es", "de", "it", "pt", "ar"))
        console.print(f"Sous-titres automatiques : {len(auto)} langue(s) (dont {common or '…'}) "
                      "— ajoutez --auto-subs")
    if only_subs:
        console.print(f"Exemple : vdm subs {a.ref} fr,en --auto --embed")
    else:
        console.print(f"Exemple : vdm add -a fr --subs fr,en --auto-subs \"{escape(a.url)}\"")
    return 0


def cmd_extension(a):
    from .extension_helper import extension_dir, open_setup
    browser = open_setup()
    console.print(f"Dossier de l'extension : {escape(str(extension_dir()))}")
    if browser:
        console.print(f"Page des extensions de {browser} ouverte, avec le dossier et un mémo des étapes.")
    else:
        console.print("Aucun navigateur compatible trouvé (Chrome, Edge, Brave ou Firefox) : "
                      "le dossier et le mémo des étapes sont ouverts.")
    return 0


def cmd_gui(a):
    try:
        from .gui import run
    except ImportError as e:
        console.print(f"[red]Interface graphique indisponible :[/] {escape(str(e))}\n"
                      "Installez-la avec : pip install PySide6-Essentials")
        return 1
    return run(a.port)


# --- analyse des arguments --------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(prog="vdm", description="Gestionnaire de téléchargements "
                                "multi-connexions (fichiers et sites vidéo).")
    p.add_argument("--version", action="version", version=f"vdm {__version__}")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="commande")

    def dl_options(sp):
        sp.add_argument("-o", "--output", help="dossier de destination (sinon : dossier de la catégorie)")
        sp.add_argument("-n", "--connections", type=int, help="connexions par fichier (8)")
        sp.add_argument("-q", "--quality", default="best", choices=ytdl.QUALITIES,
                        help="qualité pour les sites vidéo")
        sp.add_argument("--kind", default="auto", choices=("auto", "http", "ytdl"),
                        help="forcer le moteur : fichier direct ou site vidéo")
        sp.add_argument("--referer", help="en-tête Referer à envoyer")
        sp.add_argument("--playlist", action="store_true",
                        help="URL vidéo + playlist : télécharger toute la playlist")
        sp.add_argument("-a", "--audio-lang", metavar="LANGUE",
                        help="langue de la piste audio (fr, en, es…) si la vidéo en propose plusieurs ; "
                             "« \"\" » = langue d'origine. Voir `vdm langs URL`")
        sp.add_argument("--subs", metavar="LANGUES",
                        help="sous-titres à télécharger, ex. fr,en (« \"\" » = aucun). Voir `vdm langs URL`")
        sp.add_argument("--auto-subs", action=argparse.BooleanOptionalAction, default=None,
                        help="accepter les sous-titres automatiques (générés / traduits par le site)")
        sp.add_argument("--embed-subs", action=argparse.BooleanOptionalAction, default=None,
                        help="intégrer les sous-titres dans la vidéo au lieu de fichiers .srt")

    g = sub.add_parser("get", help="télécharger tout de suite, sans daemon")
    g.add_argument("url")
    dl_options(g)
    g.add_argument("--limit", help="limite de vitesse, ex. 2M")
    g.set_defaults(func=cmd_get)

    d = sub.add_parser("daemon", help="lancer le gestionnaire (file d'attente + API pour l'extension)")
    d.add_argument("-o", "--output", help="dossier de base")
    d.add_argument("-p", "--parallel", type=int, help="téléchargements simultanés (3)")
    d.add_argument("-n", "--connections", type=int, help="connexions par fichier (8)")
    d.add_argument("--limit", help="limite de vitesse globale, ex. 2M")
    d.set_defaults(func=cmd_daemon)

    ad = sub.add_parser("add", help="ajouter des URL à la file")
    ad.add_argument("urls", nargs="+")
    dl_options(ad)
    ad.add_argument("-s", "--scheduled", action="store_true", help="mettre dans la file planifiée")
    ad.set_defaults(func=cmd_add)

    sub.add_parser("list", help="afficher la file").set_defaults(func=cmd_list)

    for name, action, helptext in (("pause", "pause", "mettre en pause"),
                                   ("resume", "resume", "reprendre (ou relancer après erreur)"),
                                   ("remove", "remove", "retirer de la liste")):
        sp = sub.add_parser(name, help=f"{helptext} (numéros ou « all »)")
        sp.add_argument("refs", nargs="+")
        if action == "remove":
            sp.add_argument("--delete", action="store_true", help="supprimer aussi les fichiers")
        sp.set_defaults(func=lambda a, action=action: _control(a, action))

    mv = sub.add_parser("move", help="changer de file (principale / planifiée)")
    mv.add_argument("refs", nargs="+")
    mv.add_argument("--to", dest="queue", required=True, choices=(MAIN, SCHEDULED))
    mv.set_defaults(func=lambda a: _control(a, "move"))

    sc = sub.add_parser("schedule", help="planificateur de la file planifiée")
    sc.add_argument("action", nargs="?", default="show", choices=("show", "on", "off"))
    sc.add_argument("--start", help="heure de départ HH:MM")
    sc.add_argument("--stop", help="heure d'arrêt HH:MM (« aucune » : jusqu'à la fin de la file)")
    sc.add_argument("--days", help="jours : lun,mar,… ou « tous »")
    sc.add_argument("--after", choices=tuple(AFTER_ACTIONS), help="action quand la file est terminée")
    sc.set_defaults(func=cmd_schedule)

    lg = sub.add_parser("langs", help="lister les pistes audio et les sous-titres d'une vidéo")
    lg.add_argument("url")
    lg.add_argument("--referer", help=argparse.SUPPRESS)
    lg.set_defaults(func=cmd_langs)

    st = sub.add_parser("subs", help="ajouter des sous-titres à une vidéo déjà téléchargée")
    st.add_argument("ref", metavar="NUMÉRO")
    st.add_argument("langs", nargs="?", metavar="LANGUES", help="ex. fr,en (sans langue : liste les sous-titres)")
    st.add_argument("--auto", action="store_true", help="accepter les sous-titres automatiques / traduits")
    st.add_argument("--embed", action="store_true", help="intégrer dans la vidéo (sinon fichiers .srt)")
    st.add_argument("--list", action="store_true", help="lister les sous-titres disponibles")
    st.set_defaults(func=cmd_subs, referer=None)

    sub.add_parser("extension", help="aide à l'installation de l'extension navigateur").set_defaults(
        func=cmd_extension)

    sub.add_parser("gui", help="ouvrir l'interface graphique (remplace le daemon)").set_defaults(func=cmd_gui)

    sub.add_parser("clean", help="retirer les téléchargements terminés").set_defaults(func=cmd_clean)

    lim = sub.add_parser("limit", help="changer la limite de vitesse du daemon (0 = aucune)")
    lim.add_argument("rate")
    lim.set_defaults(func=cmd_limit)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        sys.exit(args.func(args) or 0)
    except (ValueError, DaemonError, DownloadError, requests.RequestException) as e:
        console.print(f"[red]Erreur :[/] {escape(str(e))}")
        sys.exit(2)

"""Téléchargement depuis les sites vidéo (YouTube, HLS, DASH…) via yt-dlp.

Les flux fragmentés (HLS/DASH) sont récupérés avec plusieurs fragments en parallèle,
l'équivalent des connexions multiples du moteur HTTP.
"""

import copy
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .engine import DownloadError, Paused

QUALITIES = ("best", "1080", "720", "480", "audio")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # codes couleur des messages de yt-dlp


def available():
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def ffmpeg_location():
    """ffmpeg du système, sinon celui fourni par le paquet imageio-ffmpeg."""
    found = os.environ.get("VDM_FFMPEG") or shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        return None


def deno_location():
    """Moteur JavaScript utilisé par yt-dlp pour les protections de YouTube (paquet pip « deno »)."""
    found = shutil.which("deno")
    if found:
        return found
    folders = [Path(sys.executable).parent, Path(sys.executable).parent / "Scripts"]
    if getattr(sys, "frozen", False):  # version installée : deno.exe est livré avec VDM
        folders.insert(0, Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    for folder in folders:
        exe = folder / ("deno.exe" if sys.platform == "win32" else "deno")
        if exe.is_file():
            return str(exe)
    return None


def _common_opts():
    """Options communes à toutes les requêtes yt-dlp."""
    deno = deno_location()
    return {"js_runtimes": {"deno": {"path": deno}}} if deno else {}


_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def check_lang(lang):
    """Code de langue (« fr », « en-US »…) ; chaîne vide = piste d'origine."""
    lang = (lang or "").strip()
    if lang and not _LANG.match(lang):
        raise ValueError(f"code de langue invalide : {lang!r} (ex. fr, en, es, en-US)")
    return lang


def parse_subs(text):
    """« fr, en » -> « fr,en » (codes vérifiés, sans doublon) ; vide = pas de sous-titres."""
    codes = [check_lang(c) for c in re.split(r"[,;\s]+", text or "") if c.strip()]
    return ",".join(dict.fromkeys(codes))


def _lang_matches(code, wanted):
    """« fr » accepte « fr », « fr-FR », « fr-CA »… mais pas « fr-orig »."""
    code, wanted = code.lower(), wanted.lower()
    return code == wanted or (code.startswith(wanted + "-") and not code.endswith("-orig"))


def _sub_regex(code):
    """Expression yt-dlp : « fr » couvre fr, fr-FR, fr-CA… (pas fr-orig)."""
    return f"{re.escape(code)}(-(?!orig$)[A-Za-z0-9-]+)?"


def _format(quality, has_ffmpeg, lang=""):
    height = "" if quality in ("best", "audio") else f"[height<={int(quality)}]"
    # piste dans la langue demandée si elle existe, sinon la piste par défaut (langue d'origine)
    audio = f"ba[language^={lang}]/ba" if lang else "ba"
    if quality == "audio":
        return f"{audio}/b"
    if has_ffmpeg:  # vidéo et audio séparés, fusionnés par ffmpeg
        if lang:  # vidéo seule (bv) : la seule piste audio sera celle choisie
            return f"bv{height}+ba[language^={lang}]/bv*{height}+ba/b{height}/b"
        return f"bv*{height}+ba/b{height}/b"
    return f"b{height}/b"  # sans ffmpeg : uniquement les formats déjà combinés


def media_tracks(url, headers=None):
    """Pistes proposées par la vidéo.

    {"audio": [(code, libellé)], "subs": [(code, libellé)], "auto": [(code, libellé)]}
    - audio : pistes audio (doublages), piste d'origine en premier ;
    - subs : sous-titres écrits par l'auteur ;
    - auto : sous-titres automatiques (générés, et souvent traduisibles dans toutes les langues).
    """
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _Logger(),
            "extract_flat": "in_playlist", "skip_download": True, **_common_opts()}
    if headers:
        opts["http_headers"] = {k: v for k, v in headers.items() if v}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False, process=False)
    if info.get("_type") in ("playlist", "multi_video"):  # on regarde le premier élément
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            return {"audio": [], "subs": [], "auto": []}
        return media_tracks(entries[0].get("url") or entries[0].get("webpage_url"), headers)

    langs = {}
    for f in info.get("formats") or []:
        if f.get("acodec") in (None, "none") or f.get("vcodec") not in (None, "none"):
            continue
        code = f.get("language")
        if not code or code in langs:
            continue
        note = (f.get("format_note") or "").split(",")[0].strip()
        langs[code] = (note or code, "original" in note or (f.get("language_preference") or 0) >= 10)
    audio = [(c, label) for c, (label, _) in sorted(langs.items(), key=lambda kv: (not kv[1][1], kv[1][0].lower()))]

    def sub_list(tracks):
        out = []
        for code, formats in (tracks or {}).items():
            if code == "live_chat" or code.endswith("-orig"):
                continue
            name = next((f.get("name") for f in formats or [] if f.get("name")), None) or code
            out.append((code, name))
        return sorted(out, key=lambda t: t[1].lower())

    return {"audio": audio, "subs": sub_list(info.get("subtitles")),
            "auto": sub_list(info.get("automatic_captions"))}


def audio_languages(url, headers=None):
    return media_tracks(url, headers)["audio"]


class PlaylistFound(Exception):
    """L'URL est une playlist : le gestionnaire la remplace par un téléchargement par élément."""

    def __init__(self, title, entries):
        super().__init__(f"playlist de {len(entries)} élément(s)")
        self.title = title
        self.entries = entries    # [(index, url, titre)]


class _Logger:
    def __init__(self):
        self.last_error = None

    def debug(self, msg):
        pass

    info = warning = debug

    def error(self, msg):
        self.last_error = _ANSI.sub("", msg).removeprefix("ERROR: ")


class YtdlDownload:
    def __init__(self, url, dest_dir, *, quality="best", connections=8, headers=None,
                 rate_limit=None, stop_event=None, playlist=False, prefix="", audio_lang="",
                 subs="", subs_auto=False, subs_embed=False, title_hint=None):
        self.url = url
        # titre de l'onglet transmis par l'extension : sert de nom aux flux anonymes
        # (« index.m3u8 »…) pour lesquels yt-dlp ne trouve pas de vrai titre
        self.title_hint = (title_hint or "").strip() or None
        self.dest_dir = Path(dest_dir)
        self.quality = quality if quality in QUALITIES else "best"
        self.connections = max(1, connections)
        self.headers = {k: v for k, v in (headers or {}).items() if v}
        self.rate_limit = rate_limit
        self.stop_event = stop_event or threading.Event()
        self.playlist = playlist  # pour « watch?v=…&list=… » : toute la playlist plutôt que la vidéo
        self.prefix = prefix      # « 03 - » pour garder l'ordre d'une playlist
        self.audio_lang = check_lang(audio_lang)
        self.subs = parse_subs(subs)
        self.subs_auto = bool(subs_auto)    # accepter les sous-titres automatiques / traduits
        self.subs_embed = bool(subs_embed)  # intégrer dans la vidéo plutôt que des .srt à côté
        self._track_notes = []    # remarques sur les pistes trouvées (langue indisponible…)
        self._subs_failed = None  # raison de l'abandon des sous-titres, le cas échéant
        self.path = None
        self.title = None
        self.active_connections = 0
        self._files = {}           # fichier -> (octets reçus, total)
        self._expected = None      # taille totale annoncée (vidéo + audio)
        self._log = _Logger()

    @property
    def note(self):
        """Remarques non bloquantes affichées à l'utilisateur."""
        notes = list(self._track_notes)
        if self._subs_failed:
            notes.append(self._subs_failed)
        return " ; ".join(notes) or None

    @property
    def downloaded(self):
        return sum(done for done, _ in self._files.values())

    @property
    def size(self):
        known = sum(total for _, total in self._files.values())
        return max(self._expected or 0, known) or None

    def pause(self):
        self.stop_event.set()

    def _hook(self, d):
        if self.stop_event.is_set():
            raise Paused()
        info = d.get("info_dict") or {}
        if self.title is None and info.get("title"):
            self.title = info["title"]
        if self._expected is None and info.get("requested_formats"):
            sizes = [f.get("filesize") or f.get("filesize_approx") for f in info["requested_formats"]]
            if all(sizes):
                self._expected = int(sum(sizes))
        name = d.get("filename")
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if d["status"] == "downloading":
            self._files[name] = (d.get("downloaded_bytes") or 0, int(total))
            self.active_connections = self.connections if d.get("fragment_count") else 1
        elif d["status"] == "finished":
            done = d.get("downloaded_bytes") or total
            self._files[name] = (done, max(int(total), done))
            self.active_connections = 0

    def _options(self, ffmpeg, with_subs):
        suffix = f" [{self.audio_lang}]" if self.audio_lang else ""
        opts = {
            # la langue dans le nom permet de garder plusieurs versions de la même vidéo
            "outtmpl": str(self.dest_dir / (self.prefix.replace("%", "%%") + "%(title).150B [%(id)s]"
                                            + suffix + ".%(ext)s")),
            "format": _format(self.quality, bool(ffmpeg), self.audio_lang),
            "merge_output_format": "mp4/mkv",
            "noplaylist": not self.playlist,
            "extract_flat": "in_playlist",  # playlist : on ne liste que les URL des éléments
            "windowsfilenames": True,
            "continuedl": True,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": self.connections,
            "progress_hooks": [self._hook],
            "logger": self._log,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            **_common_opts(),
        }
        pps = []
        if with_subs:
            opts.update(writesubtitles=True, writeautomaticsub=self.subs_auto,
                        subtitlesformat="srt/vtt/best",
                        subtitleslangs=[_sub_regex(c) for c in self.subs.split(",")])
            if ffmpeg:
                pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
                if self.subs_embed and self.quality != "audio":
                    pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
        if ffmpeg:
            opts["ffmpeg_location"] = ffmpeg
            if self.quality == "audio":
                pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3"})
        if pps:
            opts["postprocessors"] = pps
        if self.headers:
            opts["http_headers"] = self.headers
        if self.rate_limit:
            opts["ratelimit"] = self.rate_limit
        return opts

    def run(self):
        ffmpeg = ffmpeg_location()
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            try:
                self._download_retrying(ffmpeg, with_subs=bool(self.subs))
            except DownloadError as e:
                # un sous-titre refusé (YouTube limite souvent ces requêtes) ne doit pas faire
                # échouer la vidéo : on recommence sans sous-titres
                if not self.subs or "subtitle" not in str(e).lower():
                    raise
                reason = "trop de requêtes, réessayez plus tard" if "429" in str(e) else "refusés par le site"
                self._subs_failed = f"sous-titres non téléchargés ({reason})"
                self._download_retrying(ffmpeg, with_subs=False)
        except (Paused, PlaylistFound):
            raise
        except Exception as e:
            if self.stop_event.is_set():
                raise Paused() from None
            msg = _ANSI.sub("", self._log.last_error or str(e))
            if "Unsupported URL" in msg:
                msg = "aucune vidéo reconnue sur cette page (site non pris en charge)"
            elif "not a bot" in msg or "Sign in to confirm" in msg:
                msg = ("YouTube bloque temporairement cette connexion (trop de requêtes) : "
                       "réessayez dans quelques minutes à quelques heures")
            elif "403" in msg:
                msg = ("le site a refusé l'accès à la vidéo (erreur 403) malgré plusieurs essais : "
                       "réessayez plus tard avec « Reprendre »")
            elif "Requested format is not available" in msg:
                msg = ("format demandé indisponible" if ffmpeg else
                       "cette vidéo n'existe qu'en flux vidéo et audio séparés : ffmpeg est nécessaire "
                       "(pip install imageio-ffmpeg)")
            raise DownloadError(msg) from None
        return self.path

    def _download_retrying(self, ffmpeg, with_subs, attempts=3):
        """Les adresses des flux YouTube expirent ou sont parfois refusées (403) : on redemande
        des adresses neuves au site et on recommence, là où le téléchargement s'était arrêté."""
        for attempt in range(1, attempts + 1):
            try:
                return self._download(self._options(ffmpeg, with_subs))
            except DownloadError as e:
                if "403" not in str(e) or attempt == attempts:
                    raise
                if self.stop_event.wait(5 * attempt):  # pause demandée pendant l'attente
                    raise Paused() from None

    def _download(self, opts):
        import yt_dlp

        self._log.last_error = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False)
                if info.get("_type") in ("playlist", "multi_video"):
                    raise PlaylistFound(info.get("title"), _entries(info))
                clean = ydl.sanitize_info(info, True)
                if self.title_hint and info.get("extractor_key") == "Generic":
                    clean["title"] = self.title_hint  # flux sans titre : nom de l'onglet
                self.title = clean.get("title") or self.title
                self._check_tracks(info, opts.get("writesubtitles"))
                # même méthode que yt-dlp pour télécharger depuis des infos déjà extraites
                info = ydl.process_ie_result(clean, download=True)
                downloads = info.get("requested_downloads") or [{}]
                self.path = Path(downloads[0].get("filepath") or ydl.prepare_filename(info))
        except (Paused, PlaylistFound):
            raise
        except Exception as e:
            if self.stop_event.is_set():
                raise Paused() from None
            raise DownloadError(_ANSI.sub("", self._log.last_error or str(e))) from None

    def _check_tracks(self, info, with_subs):
        notes = []
        if self.audio_lang:
            formats = info.get("requested_formats") or [info]
            audio = [f for f in formats if f.get("acodec") not in (None, "none")]
            got = (audio[0].get("language") or "") if audio else ""
            if not got.lower().startswith(self.audio_lang.lower()):
                notes.append(f"piste audio « {self.audio_lang} » indisponible : langue d'origine utilisée")
        if with_subs:
            found = list((info.get("requested_subtitles") or {}).keys())
            missing = [c for c in self.subs.split(",") if not any(_lang_matches(k, c) for k in found)]
            if missing:
                hint = "" if self.subs_auto else " (essayez les sous-titres automatiques)"
                notes.append(f"sous-titres indisponibles : {', '.join(missing)}{hint}")
        self._track_notes = notes


def _entries(info):
    entries = []
    for i, e in enumerate(info.get("entries") or [], 1):
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if url.startswith(("http://", "https://")):
            entries.append((i, url, e.get("title")))
    return entries


# --- sous-titres pour une vidéo déjà téléchargée -----------------------------------

# codes ISO 639-2 attendus dans les métadonnées des conteneurs (mp4 surtout)
_ISO3 = {"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita", "pt": "por", "ar": "ara",
         "hi": "hin", "ja": "jpn", "ko": "kor", "zh": "zho", "ru": "rus", "id": "ind", "tr": "tur",
         "pl": "pol", "nl": "nld", "uk": "ukr", "vi": "vie", "th": "tha", "sv": "swe", "mg": "mlg"}
# codec de sous-titres accepté par chaque conteneur
_SUB_CODEC = {".mp4": "mov_text", ".m4v": "mov_text", ".mov": "mov_text", ".mkv": "srt", ".webm": "webvtt"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # pas de console noire depuis l'interface


def can_embed(path):
    return Path(path).suffix.lower() in _SUB_CODEC


def _ffmpeg(args):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW)


def fetch_subtitles(url, video_path, langs, auto=False, embed=False, headers=None):
    """Télécharge seulement des sous-titres pour une vidéo déjà sur le disque.

    Les fichiers sont nommés « <vidéo>.<langue>.srt » pour que les lecteurs (VLC…) les
    chargent tout seuls. Avec embed=True, ils sont intégrés dans la vidéo puis supprimés.
    Renvoie (langues ajoutées, {langue: raison de l'échec}, intégrés ?).
    """
    import yt_dlp

    video = Path(video_path)
    if not video.exists():
        raise DownloadError(f"fichier introuvable : {video}")
    codes = [c for c in parse_subs(langs).split(",") if c]
    if not codes:
        raise ValueError("aucune langue de sous-titres choisie")
    ffmpeg = ffmpeg_location()
    log = _Logger()
    opts = {"quiet": True, "no_warnings": True, "logger": log, "noplaylist": True,
            "skip_download": True, "writesubtitles": True, "writeautomaticsub": bool(auto),
            "subtitlesformat": "srt/vtt/best",
            "outtmpl": str(video.with_suffix("")).replace("%", "%%") + ".%(ext)s", **_common_opts()}
    if headers:
        opts["http_headers"] = {k: v for k, v in headers.items() if v}

    added, failed, files = [], {}, []
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as e:
            msg = _ANSI.sub("", log.last_error or str(e))
            if "not a bot" in msg or "Sign in to confirm" in msg:
                msg = "YouTube bloque temporairement cette connexion : réessayez plus tard"
            raise DownloadError(msg) from None
        manual = set((info.get("subtitles") or {}).keys())
        available = manual | (set((info.get("automatic_captions") or {}).keys()) if auto else set())
        clean = ydl.sanitize_info(info, True)
        first = True
        for code in codes:
            if not any(_lang_matches(k, code) for k in available):
                failed[code] = "indisponible" + ("" if auto else ", essayez les automatiques")
                continue
            if not first:
                time.sleep(2)  # YouTube refuse les demandes trop rapprochées
            first = False
            ydl.params["subtitleslangs"] = [_sub_regex(code)]
            log.last_error = None
            try:
                result = ydl.process_ie_result(copy.deepcopy(clean), download=True)
            except Exception as e:
                msg = _ANSI.sub("", log.last_error or str(e))
                failed[code] = "trop de requêtes, réessayez plus tard" if "429" in msg else msg[:120]
                continue
            got = False
            for lang, sub in (result.get("requested_subtitles") or {}).items():
                path = Path(sub.get("filepath") or "")
                if path.is_file():
                    files.append((lang, path, lang not in manual))
                    got = True
            if got:
                added.append(code)
            else:
                failed[code] = "aucun fichier reçu"

    # conversion en .srt (format le plus compatible)
    tracks = []
    for lang, path, is_auto in files:
        if path.suffix.lower() != ".srt" and ffmpeg:
            srt = path.with_suffix(".srt")
            if _ffmpeg([ffmpeg, "-y", "-loglevel", "error", "-i", str(path), str(srt)]).returncode == 0:
                path.unlink(missing_ok=True)
                path = srt
        tracks.append((lang, path, is_auto))

    embedded = False
    if embed and tracks:
        if not ffmpeg:
            failed["intégration"] = "ffmpeg introuvable, fichiers .srt conservés"
        elif not can_embed(video):
            failed["intégration"] = f"impossible dans un fichier {video.suffix}, fichiers .srt conservés"
        else:
            embed_subtitles(video, tracks, ffmpeg)
            for _, path, _ in tracks:
                path.unlink(missing_ok=True)
            embedded = True
    return added, failed, embedded


def embed_subtitles(video, tracks, ffmpeg):
    """Ajoute des pistes de sous-titres à la vidéo (sans réencoder l'image ni le son)."""
    video = Path(video)
    probe = _ffmpeg([ffmpeg, "-hide_banner", "-i", str(video)])
    existing = len(re.findall(r"^\s*Stream #0:\d+", probe.stderr, re.M))  # nouvelles pistes après celles-ci
    tmp = video.with_name(video.stem + ".vdm-tmp" + video.suffix)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", str(video)]
    for _, path, _ in tracks:
        cmd += ["-i", str(path)]
    cmd += ["-map", "0"]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:0"]
    cmd += ["-c", "copy", "-c:s", _SUB_CODEC[video.suffix.lower()]]
    for i, (lang, _, is_auto) in enumerate(tracks):
        idx = existing + i
        cmd += [f"-metadata:s:{idx}", f"language={_ISO3.get(lang.split('-')[0].lower(), 'und')}",
                f"-metadata:s:{idx}", f"title={lang}{' (automatique)' if is_auto else ''}"]
    cmd.append(str(tmp))
    result = _ffmpeg(cmd)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise DownloadError("échec de l'intégration des sous-titres : " + result.stderr.strip()[-200:])
    os.replace(tmp, video)

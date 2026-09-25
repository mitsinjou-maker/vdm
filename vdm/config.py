"""Réglages persistants (%APPDATA%\\vdm\\config.json) et catégories automatiques."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .util import APP_DIR, DEFAULT_DEST

CONFIG_PATH = APP_DIR / "config.json"

# catégorie -> extensions ; chaque catégorie a son sous-dossier dans le dossier de base
CATEGORIES = {
    "Vidéos": "mp4 m4v mkv webm avi mov wmv flv mpg mpeg 3gp ts m2ts ogv",
    "Musique": "mp3 m4a aac flac wav ogg oga opus wma aiff",
    "Images": "jpg jpeg png gif webp bmp svg tif tiff heic avif",
    "Documents": "pdf doc docx xls xlsx ppt pptx odt ods odp txt rtf epub csv",
    "Compressés": "zip rar 7z tar gz bz2 xz tgz zst",
    "Programmes": "exe msi apk dmg deb rpm appimage iso img",
}
OTHER = "Autres"
CATEGORY_NAMES = [*CATEGORIES, OTHER]
_BY_EXT = {ext: cat for cat, exts in CATEGORIES.items() for ext in exts.split()}


def category_for(filename=None, content_type=None):
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in _BY_EXT:
        return _BY_EXT[ext]
    ctype = (content_type or "").lower()
    for prefix, cat in (("video/", "Vidéos"), ("audio/", "Musique"), ("image/", "Images")):
        if ctype.startswith(prefix):
            return cat
    return OTHER


DAYS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
# langues proposées dans les menus (tout code ISO 639 fonctionne)
AUDIO_LANGS = [("", "Langue d'origine"), ("fr", "Français"), ("en", "Anglais"), ("es", "Espagnol"),
               ("de", "Allemand"), ("it", "Italien"), ("pt", "Portugais"), ("ar", "Arabe"),
               ("hi", "Hindi"), ("ja", "Japonais"), ("ko", "Coréen"), ("zh", "Chinois"),
               ("ru", "Russe"), ("id", "Indonésien"), ("tr", "Turc"), ("pl", "Polonais")]

AFTER_ACTIONS = {"rien": "Ne rien faire", "veille": "Mettre en veille", "arret": "Éteindre l'ordinateur"}


@dataclass
class Schedule:
    enabled: bool = False
    start: str = "02:00"
    stop: str | None = "07:00"    # None : on continue jusqu'à vider la file planifiée
    days: list = field(default_factory=list)  # 0 = lundi ; liste vide = tous les jours
    after: str = "rien"           # action quand la file planifiée est terminée


@dataclass
class Config:
    dest: str = str(DEFAULT_DEST)
    parallel: int = 3
    connections: int = 8
    speed_limit: int | None = None
    categorize: bool = True
    audio_lang: str = ""          # langue audio préférée pour les sites vidéo ("" = d'origine)
    subs: str = ""                # sous-titres à télécharger par défaut, ex. "fr,en" ("" = aucun)
    subs_auto: bool = False       # accepter les sous-titres automatiques (générés / traduits)
    subs_embed: bool = False      # intégrer les sous-titres dans la vidéo (sinon fichiers .srt)
    schedule: Schedule = field(default_factory=Schedule)

    @classmethod
    def load(cls, path=CONFIG_PATH):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        sched = data.pop("schedule", None) or {}
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__},
                   schedule=Schedule(**{k: v for k, v in sched.items() if k in Schedule.__dataclass_fields__}))

    def save(self, path=CONFIG_PATH):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

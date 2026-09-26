"""Interface graphique (PySide6) : fenêtre façon IDM par-dessus le même moteur.

La fenêtre embarque le gestionnaire et l'API locale : elle remplace `vdm daemon`,
et l'extension comme les commandes `vdm add/list/pause…` continuent de fonctionner.
"""

import re
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QLibraryInfo, QModelIndex, QObject, QPointF, QRectF,
                            QSortFilterProxyModel, Qt, QTime, QTimer, QTranslator, QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QDesktopServices, QGuiApplication, QIcon, QKeySequence,
                           QPainter, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QSpinBox, QSplitter,
                               QStyle, QStyledItemDelegate, QStyleOptionProgressBar, QSystemTrayIcon,
                               QTableView, QTimeEdit, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import __version__, ytdl
from .config import AFTER_ACTIONS, AUDIO_LANGS, CATEGORY_NAMES, DAYS, Config, Schedule
from .engine import HttpDownload
from .manager import DONE, DOWNLOADING, ERROR, MAIN, PAUSED, QUEUED, SCHEDULED, Manager  # noqa: F401
from .server import serve
from .util import human_size, human_time

COLS = ["Nom", "Taille", "Progression", "Vitesse", "Reste", "État", "Catégorie", "File", "Ajouté"]
C_NAME, C_SIZE, C_PROGRESS, C_SPEED, C_ETA, C_STATUS, C_CAT, C_QUEUE, C_ADDED = range(len(COLS))
SORT_ROLE = Qt.UserRole + 1
PROGRESS_ROLE = Qt.UserRole + 2
JOB_ROLE = Qt.UserRole + 3

STATUS_COLOR = {QUEUED: "#b7791f", DOWNLOADING: "#1f7ae0", PAUSED: "#8e44ad", DONE: "#1a9e55", ERROR: "#d93025"}

FILTERS = [
    ("Tous les téléchargements", lambda j: True),
    ("En cours", lambda j: j["status"] == DOWNLOADING),
    ("Non terminés", lambda j: j["status"] != DONE),
    ("Terminés", lambda j: j["status"] == DONE),
    ("Erreurs", lambda j: j["status"] == ERROR),
    ("File planifiée", lambda j: j["queue"] == SCHEDULED and j["status"] != DONE),
] + [(cat, lambda j, c=cat: j["category"] == c) for cat in CATEGORY_NAMES]


def app_icon():
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1f7ae0"))
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(Qt.white)
    p.drawPolygon(QPolygonF([QPointF(25, 13), QPointF(39, 13), QPointF(39, 31), QPointF(49, 31),
                             QPointF(32, 50), QPointF(15, 31), QPointF(25, 31)]))
    p.end()
    return QIcon(pm)


def _eta(j):
    if j["status"] != DOWNLOADING or not j["size"] or not j["speed"]:
        return None
    return (j["size"] - j["downloaded"]) / j["speed"]


def _progress(j):
    if j["status"] == DONE:
        return 100.0
    return 100.0 * j["downloaded"] / j["size"] if j["size"] else -1.0


# --- modèle de la liste --------------------------------------------------------------

class JobsModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section]
        return None

    def set_jobs(self, jobs):
        """Renvoie True si la liste a été reconstruite (la sélection doit être restaurée)."""
        if [j["id"] for j in jobs] == [r["id"] for r in self.rows]:
            self.rows = jobs
            if jobs:
                self.dataChanged.emit(self.index(0, 0), self.index(len(jobs) - 1, len(COLS) - 1))
            return False
        self.beginResetModel()
        self.rows = jobs
        self.endResetModel()
        return True

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        j, c = self.rows[index.row()], index.column()
        if role == JOB_ROLE:
            return j
        if role == PROGRESS_ROLE:
            return _progress(j)
        if role == SORT_ROLE:
            return {C_SIZE: j["size"] or 0, C_PROGRESS: _progress(j), C_SPEED: j["speed"],
                    C_ETA: _eta(j) or 0, C_ADDED: j["added"]}.get(c, self.data(index, Qt.DisplayRole) or "")
        if role == Qt.ToolTipRole:
            if c == C_STATUS and (j["error"] or j["note"]):
                return j["error"] or j["note"]
            return j["url"]
        if role == Qt.ForegroundRole and c == C_STATUS:
            return QBrush(QColor(STATUS_COLOR.get(j["status"], "#888")))
        if role == Qt.TextAlignmentRole and c in (C_SIZE, C_SPEED, C_ETA):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role != Qt.DisplayRole:
            return None
        running = j["status"] == DOWNLOADING
        if c == C_NAME:
            return j["name"]
        if c == C_SIZE:
            return human_size(j["size"]) if j["size"] else (human_size(j["downloaded"]) if j["downloaded"] else "")
        if c == C_SPEED:
            return f"{human_size(j['speed'])}/s" if running else ""
        if c == C_ETA:
            return human_time(_eta(j)) if running else ""
        if c == C_STATUS:
            text = j["status"]
            if running and j["active"]:
                text += f" ({j['active']} cx)"
            if j["status"] == ERROR and j["error"]:
                text += f" : {j['error']}"
            elif j["note"]:
                text += f" · {j['note']}"
            return text
        if c == C_CAT:
            return j["category"] or ""
        if c == C_QUEUE:
            return "⏰ planifiée" if j["queue"] == SCHEDULED else ""
        if c == C_ADDED:
            return datetime.fromtimestamp(j["added"]).strftime("%d/%m %H:%M")
        return None


class JobFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.predicate = FILTERS[0][1]
        self.text = ""
        self.setSortRole(SORT_ROLE)

    def set_predicate(self, fn):
        self.predicate = fn
        self.invalidateFilter()

    def set_text(self, text):
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        j = self.sourceModel().rows[row]
        if not self.predicate(j):
            return False
        return not self.text or self.text in j["name"].lower() or self.text in j["url"].lower()


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        value = index.data(PROGRESS_ROLE)
        running = index.data(JOB_ROLE)["status"] == DOWNLOADING
        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(3, 4, -3, -4)
        # taille inconnue : barre animée (0/0) pendant le téléchargement, vide sinon
        opt.minimum, opt.maximum = 0, 0 if value < 0 and running else 100
        opt.progress = int(value) if value >= 0 else 0
        opt.text = f"{value:.1f} %" if value >= 0 else ""
        opt.textVisible = True
        opt.state = option.state | QStyle.State_Enabled | QStyle.State_Horizontal
        QApplication.style().drawControl(QStyle.CE_ProgressBar, opt, painter)


class SegmentBar(QWidget):
    """Carte des segments : ce que chaque connexion a déjà récupéré."""

    def __init__(self):
        super().__init__()
        self.cells = []
        self.setMinimumHeight(20)

    def set_cells(self, cells):
        self.cells = cells
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.fillRect(r, self.palette().base())
        if self.cells:
            w = r.width() / len(self.cells)
            done, partial = QColor("#1a9e55"), QColor("#7fd1a5")
            for i, c in enumerate(self.cells):
                if c > 0:
                    p.fillRect(QRectF(r.x() + i * w, r.y(), w + 0.6, r.height()), done if c >= 0.999 else partial)
        p.setPen(self.palette().mid().color())
        p.drawRect(r)


# --- boîtes de dialogue --------------------------------------------------------------

def _browse(parent, line_edit):
    folder = QFileDialog.getExistingDirectory(parent, "Choisir un dossier", line_edit.text() or str(Path.home()))
    if folder:
        line_edit.setText(folder)


def _lang_combo(default=""):
    """Liste modifiable : langues courantes, ou tout code saisi à la main (ex. « pt-BR »)."""
    combo = QComboBox(editable=True)
    _fill_langs(combo, [(code, label) for code, label in AUDIO_LANGS if code], default)
    return combo


def _fill_langs(combo, langs, selected=""):
    combo.clear()
    combo.addItem("Langue d'origine", "")
    for code, label in langs:
        combo.addItem(f"{label} ({code})", code)
    for i in range(combo.count()):  # « fr » sélectionne aussi « French (FR) (fr-FR) »
        code = combo.itemData(i)
        if code == selected or (selected and code and code.lower().startswith(selected.lower() + "-")):
            combo.setCurrentIndex(i)
            return
    if selected:
        combo.setEditText(selected)


def _combo_lang(combo):
    text = combo.currentText().strip()
    i = combo.findText(text)
    if i >= 0:
        return combo.itemData(i)
    m = re.search(r"\(([^)]+)\)\s*$", text)
    return ytdl.check_lang(m.group(1) if m else text)


class SubsPicker(QWidget):
    """Codes des sous-titres (« fr, en ») + menu de choix + options automatiques / intégrés."""

    COMMON = [(code, label) for code, label in AUDIO_LANGS if code]

    def __init__(self, subs="", auto=False, embed=False):
        super().__init__()
        self.edit = QLineEdit(subs.replace(",", ", "))
        self.edit.setPlaceholderText("aucun — ex. fr, en")
        self.pick = QToolButton(text="Choisir ▾", popupMode=QToolButton.InstantPopup)
        self.auto = QCheckBox("Accepter les sous-titres automatiques (générés ou traduits)")
        self.auto.setChecked(auto)
        self.embed = QCheckBox("Intégrer dans la vidéo (sinon fichiers .srt à côté)")
        self.embed.setChecked(embed)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(self.pick)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addWidget(self.auto)
        layout.addWidget(self.embed)
        self.set_tracks(None)
        self.edit.textChanged.connect(self._sync_checks)

    def codes(self):
        return ytdl.parse_subs(self.edit.text()).split(",") if self.edit.text().strip() else []

    def value(self):
        return ytdl.parse_subs(self.edit.text())

    def set_tracks(self, tracks):
        """tracks = résultat de ytdl.media_tracks, ou None pour la liste des langues courantes."""
        menu = QMenu(self)
        if tracks is None:
            items = [(code, label, False) for code, label in self.COMMON]
        else:
            items = [(code, label, False) for code, label in tracks["subs"]]
            common = {code for code, _ in self.COMMON}
            auto = [(c, l, True) for c, l in tracks["auto"] if c in common]
            if items and auto:
                items.append(None)
            items += auto
        if not items:
            act = menu.addAction("Aucun sous-titre disponible")
            act.setEnabled(False)
        for item in items:
            if item is None:
                menu.addSeparator()
                continue
            code, label, is_auto = item
            act = menu.addAction(f"{label} ({code})" + ("  · automatique" if is_auto else ""))
            act.setCheckable(True)
            act.setData((code, is_auto))
            act.toggled.connect(lambda on, c=code, a=is_auto: self._toggle(c, a, on))
        self.pick.setMenu(menu)
        self._sync_checks()

    def _toggle(self, code, is_auto, on):
        codes = [c for c in self.codes() if c != code]
        if on:
            codes.append(code)
            if is_auto:
                self.auto.setChecked(True)
        self.edit.setText(", ".join(codes))

    def _sync_checks(self):
        codes = set(self.codes()) if self._valid() else set()
        for act in self.pick.menu().actions():
            if act.isCheckable():
                act.blockSignals(True)
                act.setChecked(act.data()[0] in codes)
                act.blockSignals(False)

    def _valid(self):
        try:
            ytdl.parse_subs(self.edit.text())
            return True
        except ValueError:
            return False


class _LangFetcher(QObject):
    done = Signal(object, str)   # (liste des langues ou None, message d'erreur)


class AddDialog(QDialog):
    def __init__(self, parent, text="", config=None):
        super().__init__(parent)
        config = config or Config()
        default_lang = config.audio_lang
        self.subs = SubsPicker(config.subs, config.subs_auto, config.subs_embed)
        self.setWindowTitle("Ajouter des téléchargements")
        self.setMinimumWidth(560)
        self.urls = QPlainTextEdit(text)
        self.urls.setPlaceholderText("Une URL par ligne : fichier direct, page vidéo, playlist, flux .m3u8…")
        self.quality = QComboBox()
        for value, label in (("best", "Meilleure"), ("1080", "1080p"), ("720", "720p"), ("480", "480p"),
                             ("audio", "Audio seul (MP3 si ffmpeg)")):
            self.quality.addItem(label, value)
        self.lang = _lang_combo(default_lang)
        self.detect = QPushButton("Voir les pistes de la vidéo")
        self.detect.clicked.connect(self.detect_langs)
        self.lang_hint = QLabel("Pour les vidéos doublées (YouTube…). Sinon, la piste d'origine est gardée. "
                                "Le bouton liste les langues audio et les sous-titres de la vidéo.")
        self.lang_hint.setStyleSheet("color: gray")
        self.lang_hint.setWordWrap(True)
        self._fetcher = _LangFetcher(self)
        self._fetcher.done.connect(self._langs_found)
        lang_row = QHBoxLayout()
        lang_row.addWidget(self.lang, 1)
        lang_row.addWidget(self.detect)
        self.playlist = QCheckBox("Si l'URL d'une vidéo contient une playlist, télécharger toute la playlist")
        self.queue = QComboBox()
        self.queue.addItem("File principale (tout de suite)", MAIN)
        self.queue.addItem("File planifiée (selon le planificateur)", SCHEDULED)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Automatique : sous-dossier de la catégorie")
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: _browse(self, self.folder))
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        folder_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("Qualité (sites vidéo) :", self.quality)
        form.addRow("Langue audio :", lang_row)
        form.addRow("", self.lang_hint)
        form.addRow("Sous-titres :", self.subs)
        form.addRow("", self.playlist)
        form.addRow("File :", self.queue)
        form.addRow("Dossier :", folder_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Télécharger")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Adresses :"))
        layout.addWidget(self.urls)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _urls(self):
        return [u.strip() for u in self.urls.toPlainText().split() if u.strip().startswith(("http://", "https://"))]

    def _accept(self):
        try:
            _combo_lang(self.lang)
            self.subs.value()
        except ValueError as e:
            QMessageBox.warning(self, "Langue", str(e))
            return
        self.accept()

    def detect_langs(self):
        urls = self._urls()
        if not urls:
            self.lang_hint.setText("Collez d'abord l'adresse de la vidéo.")
            return
        self.detect.setEnabled(False)
        self.detect.setText("Recherche…")
        fetcher = self._fetcher

        def work():
            try:
                result, error = ytdl.media_tracks(urls[0]), ""
            except Exception as e:  # noqa: BLE001 — affiché dans la fenêtre
                result, error = None, str(e)
            try:
                fetcher.done.emit(result, error)
            except RuntimeError:  # fenêtre déjà fermée
                pass

        threading.Thread(target=work, daemon=True).start()

    def _langs_found(self, tracks, error):
        self.detect.setEnabled(True)
        self.detect.setText("Voir les pistes de la vidéo")
        if tracks is None:
            self.lang_hint.setText(f"Impossible de lire les pistes : {error[:150]}")
            return
        langs = tracks["audio"]
        parts = []
        if langs:
            current = _combo_lang(self.lang) if self.lang.currentText() else ""
            _fill_langs(self.lang, langs, current)
            parts.append(f"{len(langs)} piste(s) audio")
        else:
            parts.append("une seule langue audio")
        parts.append(f"{len(tracks['subs'])} sous-titre(s)")
        if tracks["auto"]:
            parts.append(f"{len(tracks['auto'])} sous-titre(s) automatique(s)")
        self.subs.set_tracks(tracks)
        self.lang_hint.setText("Cette vidéo propose : " + ", ".join(parts) + ". Menu « Choisir ▾ » pour les sous-titres.")

    def values(self):
        return self._urls(), dict(quality=self.quality.currentData(), playlist=self.playlist.isChecked(),
                                  queue=self.queue.currentData(), dest=self.folder.text().strip() or None,
                                  audio_lang=_combo_lang(self.lang), subs=self.subs.value(),
                                  subs_auto=self.subs.auto.isChecked(), subs_embed=self.subs.embed.isChecked())


class SubsDialog(QDialog):
    """Ajouter des sous-titres à une vidéo déjà téléchargée."""

    def __init__(self, parent, job, config):
        super().__init__(parent)
        self.setWindowTitle("Télécharger des sous-titres")
        self.setMinimumWidth(520)
        title = QLabel(job["name"], textFormat=Qt.PlainText, wordWrap=True)
        title.setStyleSheet("font-weight: bold")
        self.subs = SubsPicker(config.subs, config.subs_auto, False)
        self.subs.edit.clear()
        embeddable = ytdl.can_embed(job["path"] or "")
        self.subs.embed.setEnabled(embeddable)
        if not embeddable:
            self.subs.embed.setText("Intégrer dans la vidéo (impossible pour ce format : fichiers .srt)")
        self.status = QLabel("Recherche des sous-titres disponibles…", wordWrap=True)
        self.status.setStyleSheet("color: gray")
        hint = QLabel("Les fichiers .srt sont enregistrés à côté de la vidéo avec le même nom : "
                      "VLC et le lecteur de Windows les chargent automatiquement.", wordWrap=True)
        hint.setStyleSheet("color: gray")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Télécharger")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.subs)
        layout.addWidget(hint)
        layout.addWidget(buttons)

        self._fetcher = _LangFetcher(self)
        self._fetcher.done.connect(self._found)
        fetcher, url = self._fetcher, job["url"]

        def work():
            try:
                result, error = ytdl.media_tracks(url), ""
            except Exception as e:  # noqa: BLE001 — affiché dans la fenêtre
                result, error = None, str(e)
            try:
                fetcher.done.emit(result, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _found(self, tracks, error):
        if tracks is None:
            self.status.setText(f"Impossible de lister les sous-titres ({error[:120]}). "
                                "Vous pouvez quand même taper des codes de langue (fr, en…).")
            return
        self.subs.set_tracks(tracks)
        n, n_auto = len(tracks["subs"]), len(tracks["auto"])
        if not n and not n_auto:
            self.status.setText("Cette vidéo n'a aucun sous-titre.")
        else:
            self.status.setText(f"{n} sous-titre(s) de l'auteur, {n_auto} automatique(s). "
                                "Menu « Choisir ▾ » pour sélectionner.")
            if not n and n_auto:
                self.subs.auto.setChecked(True)
            self.subs.pick.showMenu()

    def _accept(self):
        try:
            if not self.subs.value():
                raise ValueError("Choisissez au moins une langue.")
        except ValueError as e:
            QMessageBox.warning(self, "Sous-titres", str(e))
            return
        self.accept()

    def value(self):
        return self.subs.value(), self.subs.auto.isChecked(), self.subs.embed.isChecked()


class ScheduleDialog(QDialog):
    def __init__(self, parent, schedule):
        super().__init__(parent)
        self.setWindowTitle("Planificateur")
        self.enabled = QCheckBox("Activer la file planifiée")
        self.enabled.setChecked(schedule.enabled)
        self.start = QTimeEdit(QTime.fromString(schedule.start, "H:mm"))
        self.start.setDisplayFormat("HH:mm")
        self.has_stop = QCheckBox("Arrêter à")
        self.has_stop.setChecked(bool(schedule.stop))
        self.stop = QTimeEdit(QTime.fromString(schedule.stop or "07:00", "H:mm"))
        self.stop.setDisplayFormat("HH:mm")
        self.stop.setEnabled(bool(schedule.stop))
        self.has_stop.toggled.connect(self.stop.setEnabled)
        self.days = []
        days_row = QHBoxLayout()
        for i, d in enumerate(DAYS):
            box = QCheckBox(d)
            box.setChecked(not schedule.days or i in schedule.days)
            self.days.append(box)
            days_row.addWidget(box)
        self.after = QComboBox()
        for key, label in AFTER_ACTIONS.items():
            self.after.addItem(label, key)
        self.after.setCurrentIndex(list(AFTER_ACTIONS).index(schedule.after))

        grid = QGridLayout()
        grid.addWidget(QLabel("Démarrer à"), 0, 0)
        grid.addWidget(self.start, 0, 1)
        grid.addWidget(self.has_stop, 1, 0)
        grid.addWidget(self.stop, 1, 1)
        grid.addWidget(QLabel("Jours"), 2, 0)
        grid.addLayout(days_row, 2, 1)
        grid.addWidget(QLabel("Quand la file est terminée"), 3, 0)
        grid.addWidget(self.after, 3, 1)
        hint = QLabel("Les téléchargements ajoutés à la « file planifiée » ne démarrent que pendant cette "
                      "plage horaire. Sans heure d'arrêt, la file continue jusqu'à être vide. "
                      "L'extinction est précédée d'une minute d'attente (annulable avec « shutdown /a »).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.enabled)
        layout.addLayout(grid)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _accept(self):
        if self.has_stop.isChecked() and self.start.time() == self.stop.time():
            QMessageBox.warning(self, "Planificateur", "L'heure d'arrêt doit être différente de l'heure de départ.")
            return
        if not any(b.isChecked() for b in self.days):
            QMessageBox.warning(self, "Planificateur", "Choisissez au moins un jour.")
            return
        self.accept()

    def value(self):
        days = [i for i, b in enumerate(self.days) if b.isChecked()]
        return Schedule(enabled=self.enabled.isChecked(), start=self.start.time().toString("HH:mm"),
                        stop=self.stop.time().toString("HH:mm") if self.has_stop.isChecked() else None,
                        days=[] if len(days) == 7 else days, after=self.after.currentData())


class OptionsDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.config = config
        self.dest = QLineEdit(config.dest)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(lambda: _browse(self, self.dest))
        dest_row = QHBoxLayout()
        dest_row.addWidget(self.dest)
        dest_row.addWidget(browse)
        self.categorize = QCheckBox("Ranger automatiquement par catégorie (Vidéos, Musique, Documents…)")
        self.categorize.setChecked(config.categorize)
        self.parallel = QSpinBox(minimum=1, maximum=10, value=config.parallel)
        self.connections = QSpinBox(minimum=1, maximum=32, value=config.connections)
        self.limit = QSpinBox(minimum=0, maximum=10_000_000, singleStep=100, suffix=" Ko/s")
        self.limit.setSpecialValueText("Illimitée")
        self.limit.setValue((config.speed_limit or 0) // 1024)
        self.audio_lang = _lang_combo(config.audio_lang)
        self.subs = SubsPicker(config.subs, config.subs_auto, config.subs_embed)
        form = QFormLayout()
        form.addRow("Dossier de base :", dest_row)
        form.addRow("", self.categorize)
        form.addRow("Téléchargements simultanés :", self.parallel)
        form.addRow("Connexions par fichier :", self.connections)
        form.addRow("Limite de vitesse globale :", self.limit)
        form.addRow("Langue audio préférée :", self.audio_lang)
        form.addRow("Sous-titres par défaut :", self.subs)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _accept(self):
        try:
            _combo_lang(self.audio_lang)
            self.subs.value()
        except ValueError as e:
            QMessageBox.warning(self, "Langue", str(e))
            return
        self.accept()

    def value(self):
        return replace(self.config, dest=self.dest.text().strip() or self.config.dest,
                       audio_lang=_combo_lang(self.audio_lang), subs=self.subs.value(),
                       subs_auto=self.subs.auto.isChecked(), subs_embed=self.subs.embed.isChecked(),
                       categorize=self.categorize.isChecked(), parallel=self.parallel.value(),
                       connections=self.connections.value(), speed_limit=self.limit.value() * 1024 or None)


# --- fenêtre principale --------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, manager, httpd, port):
        super().__init__()
        self.m, self.httpd, self.port = manager, httpd, port
        self.prev_status = {}
        self.quitting = False
        self.setWindowTitle(f"VDM {__version__} — Gestionnaire de téléchargements")
        self.setWindowIcon(app_icon())
        self.resize(1100, 640)

        self.model = JobsModel()
        self.proxy = JobFilter()
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(C_ADDED, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setItemDelegateForColumn(C_PROGRESS, ProgressDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.doubleClicked.connect(self.open_selected)
        self.table.selectionModel().selectionChanged.connect(self.update_details)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        for col, width in ((C_SIZE, 80), (C_PROGRESS, 140), (C_SPEED, 90), (C_ETA, 60), (C_STATUS, 150),
                           (C_CAT, 90), (C_QUEUE, 85), (C_ADDED, 90)):
            header.resizeSection(col, width)

        self.sidebar = QListWidget()
        self.sidebar.setMaximumWidth(210)
        for i, (label, _) in enumerate(FILTERS):
            if label == CATEGORY_NAMES[0]:
                sep = QListWidgetItem("CATÉGORIES")
                sep.setFlags(Qt.NoItemFlags)
                self.sidebar.addItem(sep)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            self.sidebar.addItem(item)
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentItemChanged.connect(self.filter_changed)

        self.details = self._build_details()
        right = QSplitter(Qt.Vertical)
        right.addWidget(self.table)
        right.addWidget(self.details)
        right.setStretchFactor(0, 1)
        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        self.setCentralWidget(split)

        self._build_toolbar()
        self.status_speed = QLabel()
        self.status_sched = QLabel()
        self.statusBar().addWidget(self.status_speed)
        self.statusBar().addWidget(self.status_sched)
        self.statusBar().addPermanentWidget(QLabel(f"API 127.0.0.1:{port}"))
        self._build_tray()

        self.timer = QTimer(self, interval=500, timeout=self.refresh)
        self.timer.start()
        self.refresh()

    # construction ------------------------------------------------------------

    def _action(self, text, icon, slot, shortcut=None):
        act = QAction(self.style().standardIcon(icon), text, self)
        act.triggered.connect(slot)
        if shortcut:
            act.setShortcut(QKeySequence(shortcut))
        return act

    def _build_toolbar(self):
        tb = QToolBar("Actions")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.addToolBar(tb)
        S = QStyle
        tb.addAction(self._action("Ajouter", S.SP_ArrowDown, self.add_dialog, "Ctrl+N"))
        tb.addAction(self._action("Reprendre", S.SP_MediaPlay, lambda: self.on_selected(self.m.resume)))
        tb.addAction(self._action("Pause", S.SP_MediaPause, lambda: self.on_selected(self.m.pause)))
        tb.addAction(self._action("Supprimer", S.SP_TrashIcon, lambda: self.remove_selected(False), "Delete"))
        tb.addSeparator()
        tb.addAction(self._action("Tout reprendre", S.SP_MediaSeekForward, lambda: self.m.resume("all")))
        tb.addAction(self._action("Tout arrêter", S.SP_MediaStop, lambda: self.m.pause("all")))
        tb.addAction(self._action("Nettoyer", S.SP_DialogResetButton, self.m.clean))
        tb.addSeparator()
        tb.addAction(self._action("Planificateur", S.SP_BrowserReload, self.schedule_dialog))
        tb.addAction(self._action("Options", S.SP_FileDialogDetailedView, self.options_dialog))
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        search = QLineEdit(placeholderText="Rechercher…", clearButtonEnabled=True)
        search.setMaximumWidth(220)
        search.textChanged.connect(self.proxy.set_text)
        tb.addWidget(search)

    def _build_details(self):
        box = QWidget()
        self.d_title = QLabel(textFormat=Qt.PlainText)
        self.d_title.setStyleSheet("font-weight: bold; font-size: 13px")
        self.d_url = QLabel(textFormat=Qt.PlainText, textInteractionFlags=Qt.TextSelectableByMouse)
        self.d_url.setStyleSheet("color: gray")
        self.d_path = QLabel(textFormat=Qt.PlainText, textInteractionFlags=Qt.TextSelectableByMouse)
        self.d_stats = QLabel()
        self.d_seg_label = QLabel("Carte des segments (vert : reçu)")
        self.d_seg_label.setStyleSheet("color: gray")
        self.segbar = SegmentBar()
        layout = QVBoxLayout(box)
        for w in (self.d_title, self.d_url, self.d_path, self.d_stats, self.d_seg_label, self.segbar):
            layout.addWidget(w)
        layout.addStretch()
        return box

    def _build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("VDM")
        menu = QMenu()
        menu.addAction("Afficher", self.show_window)
        menu.addAction("Ajouter…", lambda: (self.show_window(), self.add_dialog()))
        menu.addSeparator()
        menu.addAction("Tout reprendre", lambda: self.m.resume("all"))
        menu.addAction("Tout arrêter", lambda: self.m.pause("all"))
        menu.addSeparator()
        menu.addAction("Quitter", self.quit_app)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()
        self._tray_hint_shown = False

    # rafraîchissement --------------------------------------------------------

    def refresh(self):
        with self.m.lock:
            jobs = [dict(j.to_dict(), name=j.name) for j in self.m.jobs.values()]
        selected = self.selected_ids()
        if self.model.set_jobs(jobs):
            self.select_ids(selected)
        self._notify(jobs)
        self._update_counts(jobs)
        running = sum(1 for j in jobs if j["status"] == DOWNLOADING)
        speed = sum(j["speed"] for j in jobs)
        limit = f" (limite {human_size(self.m.limiter.rate)}/s)" if self.m.limiter.rate else ""
        self.status_speed.setText(f"  {running}/{self.m.max_parallel} actif(s) · {human_size(speed)}/s{limit}  ")
        self.status_sched.setText(f"⏰ {self.m.schedule_status()}")
        if self.tray:
            self.tray.setToolTip(f"VDM — {running} actif(s), {human_size(speed)}/s")
        self.update_details()

    def _notify(self, jobs):
        for j in jobs:
            before = self.prev_status.get(j["id"])
            if self.tray and before == DOWNLOADING and j["status"] in (DONE, ERROR):
                if j["status"] == DONE:
                    self.tray.showMessage("Téléchargement terminé", j["name"], QSystemTrayIcon.Information, 4000)
                else:
                    self.tray.showMessage("Échec du téléchargement", f"{j['name']}\n{j['error'] or ''}",
                                          QSystemTrayIcon.Warning, 6000)
        self.prev_status = {j["id"]: j["status"] for j in jobs}

    def _update_counts(self, jobs):
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            i = item.data(Qt.UserRole)
            if i is None:
                continue
            label, pred = FILTERS[i]
            n = sum(1 for j in jobs if pred(j))
            item.setText(f"{label} ({n})" if n else label)

    def update_details(self, *args):
        j = self.current_job()
        visible = j is not None
        for w in (self.d_title, self.d_url, self.d_path, self.d_stats):
            w.setVisible(visible)
        if not visible:
            self.d_seg_label.hide()
            self.segbar.hide()
            return
        self.d_title.setText(j["name"])
        self.d_url.setText(j["url"])
        self.d_path.setText(f"Dossier : {Path(j['path']).parent if j['path'] else j['dest']}")
        parts = [j["status"]]
        if j["size"]:
            parts.append(f"{human_size(j['downloaded'])} / {human_size(j['size'])} ({_progress(j):.1f} %)")
        if j["status"] == DOWNLOADING:
            parts += [f"{human_size(j['speed'])}/s", f"reste {human_time(_eta(j))}", f"{j['active']} connexion(s)"]
        if j["audio_lang"]:
            parts.append(f"audio : {j['audio_lang']}")
        if j["subs"]:
            parts.append(f"sous-titres : {j['subs']}" + (" (intégrés)" if j["subs_embed"] else ""))
        if j["error"] or j["note"]:
            parts.append(j["error"] or j["note"])
        dl = self.m.downloader(j["id"])
        if isinstance(dl, HttpDownload) and dl.segments:
            parts.append(f"{len(dl.segments)} segments")
            self.segbar.set_cells(dl.segment_map(max(10, self.segbar.width() // 4)))
            self.d_seg_label.show()
            self.segbar.show()
        else:
            self.d_seg_label.hide()
            self.segbar.hide()
        self.d_stats.setText(" · ".join(parts))

    # sélection ---------------------------------------------------------------

    def selected_jobs(self):
        rows = self.table.selectionModel().selectedRows()
        return [self.proxy.data(r, JOB_ROLE) for r in rows]

    def selected_ids(self):
        return {j["id"] for j in self.selected_jobs()}

    def select_ids(self, ids):
        sel = self.table.selectionModel()
        for row in range(self.proxy.rowCount()):
            idx = self.proxy.index(row, 0)
            if self.proxy.data(idx, JOB_ROLE)["id"] in ids:
                sel.select(idx, sel.Select | sel.Rows)

    def current_job(self):
        jobs = self.selected_jobs()
        return jobs[0] if len(jobs) == 1 else None

    def on_selected(self, fn, *args):
        for j in self.selected_jobs():
            try:
                fn(str(j["id"]), *args)
            except KeyError:
                pass
        self.refresh()

    # actions -----------------------------------------------------------------

    def filter_changed(self, item, _previous=None):
        if item is not None and item.data(Qt.UserRole) is not None:
            self.proxy.set_predicate(FILTERS[item.data(Qt.UserRole)][1])

    def add_dialog(self, text=None):
        if text is None:
            clip = QGuiApplication.clipboard().text().strip()
            text = clip if clip.startswith(("http://", "https://")) and "\n" not in clip else ""
        dlg = AddDialog(self, text, self.m.config)
        if dlg.exec() != QDialog.Accepted:
            return
        urls, opts = dlg.values()
        if not urls:
            QMessageBox.information(self, "Ajouter", "Aucune adresse http(s) valide.")
            return
        for url in urls:
            self.m.add(url, **opts)
        self.refresh()

    def schedule_dialog(self):
        dlg = ScheduleDialog(self, self.m.config.schedule)
        if dlg.exec() == QDialog.Accepted:
            self.m.set_schedule(dlg.value())
            self.refresh()

    def options_dialog(self):
        dlg = OptionsDialog(self, self.m.config)
        if dlg.exec() == QDialog.Accepted:
            self.m.apply_config(dlg.value())
            self.refresh()

    def remove_selected(self, delete_files):
        jobs = self.selected_jobs()
        if not jobs:
            return
        what = f"{len(jobs)} téléchargement(s)" if len(jobs) > 1 else f"« {jobs[0]['name']} »"
        extra = "\n\nLes fichiers seront aussi supprimés du disque." if delete_files else ""
        if QMessageBox.question(self, "Supprimer", f"Retirer {what} de la liste ?{extra}") != QMessageBox.Yes:
            return
        self.on_selected(self.m.remove, delete_files)

    def open_selected(self, *args):
        j = self.current_job()
        if j and j["status"] == DONE and j["path"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(j["path"]))

    def open_folder(self, j):
        path = Path(j["path"]) if j["path"] else Path(j["dest"])
        if sys.platform == "win32" and path.is_file():
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            folder = path if path.is_dir() else path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def subs_dialog(self, j):
        dlg = SubsDialog(self, j, self.m.config)
        if dlg.exec() != QDialog.Accepted:
            return
        langs, auto, embed = dlg.value()
        try:
            self.m.fetch_subs(str(j["id"]), langs, auto, embed)
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "Sous-titres", str(e))
        self.refresh()

    def context_menu(self, pos):
        jobs = self.selected_jobs()
        if not jobs:
            return
        menu = QMenu(self)
        single = jobs[0] if len(jobs) == 1 else None
        if single:
            act = menu.addAction("Ouvrir", self.open_selected)
            act.setEnabled(single["status"] == DONE)
            menu.addAction("Ouvrir le dossier", lambda: self.open_folder(single))
            act = menu.addAction("Télécharger des sous-titres…", lambda: self.subs_dialog(single))
            act.setEnabled(single["status"] == DONE and single["kind"] == "ytdl")
            menu.addSeparator()
        menu.addAction("Reprendre", lambda: self.on_selected(self.m.resume))
        menu.addAction("Pause", lambda: self.on_selected(self.m.pause))
        menu.addAction("Déplacer vers la file planifiée", lambda: self.on_selected(self.m.set_queue, SCHEDULED))
        menu.addAction("Déplacer vers la file principale", lambda: self.on_selected(self.m.set_queue, MAIN))
        menu.addSeparator()
        menu.addAction("Copier l'adresse", lambda: QGuiApplication.clipboard().setText(
            "\n".join(j["url"] for j in jobs)))
        menu.addSeparator()
        menu.addAction("Supprimer de la liste", lambda: self.remove_selected(False))
        menu.addAction("Supprimer avec les fichiers", lambda: self.remove_selected(True))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # fenêtre / fermeture -----------------------------------------------------

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.quitting or not self.tray:
            event.accept()
            if not self.quitting:
                self.quit_app()
            return
        event.ignore()  # comme IDM : on reste dans la zone de notification
        self.hide()
        if not self._tray_hint_shown:
            self.tray.showMessage("VDM continue en arrière-plan",
                                  "Clic droit sur l'icône › Quitter pour fermer complètement.",
                                  QSystemTrayIcon.Information, 4000)
            self._tray_hint_shown = True

    def quit_app(self):
        if self.quitting:
            return
        self.quitting = True
        self.timer.stop()
        self.statusBar().showMessage("Arrêt : mise en pause des téléchargements…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()
        self.m.stop()
        self.httpd.shutdown()
        QApplication.restoreOverrideCursor()
        if self.tray:
            self.tray.hide()
        QApplication.quit()


def run(port, hidden=False):
    from .cli import api  # client HTTP minimal

    app = QApplication.instance() or QApplication(sys.argv)
    translator = QTranslator(app)  # boutons standard en français (Annuler, Oui, Non…)
    if translator.load("qtbase_fr", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
    app.setApplicationName("VDM")
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    if api(port, "GET", "/api/ping"):
        QMessageBox.warning(None, "VDM", "Un daemon VDM tourne déjà (vdm daemon).\n"
                                         "Fermez-le (Ctrl+C dans son terminal) puis relancez « vdm gui ».")
        return 1
    manager = Manager(Config.load())
    try:
        httpd = serve(manager, port)
    except OSError as e:
        QMessageBox.critical(None, "VDM", f"Impossible d'écouter sur le port {port} :\n{e}")
        return 1
    manager.start()
    win = MainWindow(manager, httpd, port)
    if not (hidden and win.tray):  # --tray : seulement l'icône de la zone de notification
        win.show()
    return app.exec()

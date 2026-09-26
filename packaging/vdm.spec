# Recette PyInstaller : un dossier autonome (Python + dépendances) avec deux programmes,
#   vdm-gui.exe : interface graphique (sans console)
#   vdm.exe     : ligne de commande
# Utilisée par tools/build_installer.py ; à lancer depuis la racine du dépôt.
import os
import shutil

from PyInstaller.utils.hooks import collect_all

HERE = os.path.abspath(SPECPATH)  # noqa: F821 — fourni par PyInstaller
ROOT = os.path.dirname(HERE)
ICON = os.path.join(ROOT, "build", "vdm.ico")

datas, binaries, hiddenimports = [], [], []
# modules chargés dynamiquement ou livrés avec des fichiers annexes (JS, DLL, ffmpeg)
for package in ("yt_dlp", "yt_dlp_ejs", "curl_cffi", "imageio_ffmpeg"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

deno = shutil.which("deno")  # paquet pip « deno » : moteur JavaScript requis par yt-dlp pour YouTube
if not deno:
    raise SystemExit("deno introuvable : pip install deno")
binaries.append((deno, "."))

common = dict(pathex=[ROOT], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
              excludes=["tkinter", "unittest", "pydoc_data"], noarchive=False)

gui = Analysis([os.path.join(HERE, "vdm_gui.py")], **common)  # noqa: F821
cli = Analysis([os.path.join(HERE, "vdm_cli.py")], **common)  # noqa: F821

gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="vdm-gui",  # noqa: F821
              console=False, icon=ICON, upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="vdm",  # noqa: F821
              console=True, icon=ICON, upx=False)

COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas,  # noqa: F821
        name="VDM", upx=False)

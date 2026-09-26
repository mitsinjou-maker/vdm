"""Construit l'installateur Windows : dist/VDM-Setup-<version>.exe

    python tools/build_installer.py

Prérequis (une seule fois) :
    pip install -e . pyinstaller
    winget install JRSoftware.InnoSetup

Étapes : icône .ico → dossier autonome PyInstaller (Python + dépendances, vdm-gui.exe
et vdm.exe) → vérification rapide → installateur Inno Setup.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
FROZEN = BUILD / "pyinstaller" / "dist" / "VDM"


def step(msg):
    print(f"\n== {msg}", flush=True)


def version():
    text = (ROOT / "vdm" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def make_ico():
    """Icône multi-tailles de l'application, même dessin que celle de l'extension."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "tools"))
    from io import BytesIO

    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QGuiApplication

    from make_icons import draw

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 — requis par Qt
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    draw(256).save(buf, "PNG")
    BUILD.mkdir(exist_ok=True)
    Image.open(BytesIO(bytes(buf.data()))).save(
        BUILD / "vdm.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])


def iscc():
    candidates = [shutil.which("ISCC")]
    for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        candidates.append(os.path.join(base, "Programs", "Inno Setup 6", "ISCC.exe"))
        candidates.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    raise SystemExit("Inno Setup introuvable : winget install JRSoftware.InnoSetup")


def main():
    ver = version()
    step(f"VDM {ver} : icône")
    make_ico()

    step("PyInstaller : dossier autonome (quelques minutes)")
    subprocess.run([sys.executable, "-m", "PyInstaller", str(ROOT / "packaging" / "vdm.spec"),
                    "--noconfirm", "--clean",
                    "--distpath", str(BUILD / "pyinstaller" / "dist"),
                    "--workpath", str(BUILD / "pyinstaller" / "work")], check=True, cwd=ROOT)

    step("Vérification du programme construit")
    out = subprocess.run([str(FROZEN / "vdm.exe"), "--version"], capture_output=True, text=True, check=True)
    print(out.stdout.strip())
    if out.stdout.split()[-1] != ver:
        raise SystemExit(f"version inattendue : {out.stdout!r}")

    step("Inno Setup : installateur")
    subprocess.run([iscc(), f"/DAppVersion={ver}", "/Qp", str(ROOT / "packaging" / "vdm.iss")], check=True)
    setup = ROOT / "dist" / f"VDM-Setup-{ver}.exe"
    print(f"\nInstallateur : {setup} ({setup.stat().st_size / 2**20:.0f} Mo)")


if __name__ == "__main__":
    main()

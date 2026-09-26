"""Lancement de l'interface pour la version installée (vdm-gui.exe, sans console)."""

import argparse
import sys

from .util import APP_DIR, DEFAULT_PORT

# repris par l'installateur (AppMutex) : il ferme VDM avant une mise à jour ou une désinstallation
MUTEX_NAME = "VDM_Instance_Mutex"


def _redirect_output():
    """Sans console, les messages d'erreur vont dans %APPDATA%\\vdm\\vdm.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    APP_DIR.mkdir(parents=True, exist_ok=True)
    log = open(APP_DIR / "vdm.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — ouvert pour toute la session
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main(argv=None):
    parser = argparse.ArgumentParser(prog="VDM")
    parser.add_argument("--tray", action="store_true",
                        help="démarrer réduit dans la zone de notification (lancement avec Windows)")
    parser.add_argument("--install-extension", action="store_true",
                        help="aide à l'installation de l'extension navigateur, sans ouvrir VDM")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS)
    _redirect_output()
    args = parser.parse_args(argv)
    if args.install_extension:  # fin de l'installateur, ou raccourci du menu Démarrer
        from .extension_helper import open_setup
        open_setup()
        return 0
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)  # libéré à la fermeture du processus
    from .gui import run
    return run(args.port, hidden=args.tray)


if __name__ == "__main__":
    sys.exit(main())

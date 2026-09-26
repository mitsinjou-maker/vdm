"""Aide à l'installation de l'extension navigateur.

Les navigateurs interdisent d'installer une extension à la place de l'utilisateur :
on lui prépare tout (page des extensions de son navigateur, dossier de l'extension,
mémo des 3 clics) pour qu'il n'ait rien à chercher.
"""

import html
import os
import subprocess
import sys
from pathlib import Path

from .util import APP_DIR

# nom -> (exécutable, page des extensions, ProgId du navigateur par défaut)
BROWSERS = {
    "Chrome": ("chrome.exe", "chrome://extensions", "ChromeHTML"),
    "Edge": ("msedge.exe", "edge://extensions", "MSEdgeHTM"),
    "Brave": ("brave.exe", "brave://extensions", "BraveHTML"),
    "Firefox": ("firefox.exe", "about:debugging#/runtime/this-firefox", "FirefoxURL"),
}


def extension_dir():
    """Dossier de l'extension : à côté de VDM installé, ou dans le dépôt en développement."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "extension"
    return Path(__file__).resolve().parent.parent / "extension"


def _app_path(exe):
    """Chemin d'un navigateur d'après le registre Windows (« App Paths »)."""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                path = winreg.QueryValue(key, None)
            if path and Path(path.strip('"')).is_file():
                return path.strip('"')
        except OSError:
            continue
    return None


def installed_browsers():
    return {name: path for name, (exe, _, _) in BROWSERS.items() if (path := _app_path(exe))}


def default_browser():
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
    except (ImportError, OSError):
        return None
    return next((name for name, (_, _, pid) in BROWSERS.items() if prog_id.startswith(pid)), None)


def choose_browser(browsers):
    """Navigateur par défaut s'il accepte l'extension, sinon Chrome, Edge, Brave puis Firefox."""
    preferred = default_browser()
    if preferred in browsers:
        return preferred
    return next(iter(browsers), None)


def write_guide(folder, browser):
    """Mémo HTML (thème clair/sombre) avec les étapes et le chemin du dossier à copier."""
    path = html.escape(str(folder))
    chromium = """<ol>
<li>Activez le <b>Mode développeur</b> (interrupteur en haut à droite de la page des extensions).</li>
<li>Cliquez sur <b>Charger l'extension non empaquetée</b>.</li>
<li>Choisissez le dossier ci-dessus (collez son chemin dans la fenêtre, ou cliquez sur le dossier ouvert à côté), puis <b>Sélectionner le dossier</b>.</li>
</ol>
<p class="hint">Ensuite, épinglez l'icône VDM via le bouton 🧩 de la barre d'outils.</p>"""
    firefox = """<ol>
<li>Sur la page qui s'est ouverte, cliquez sur <b>Charger un module complémentaire temporaire…</b></li>
<li>Choisissez le fichier <b>manifest.json</b> dans le dossier ci-dessus.</li>
<li>Cliquez sur l'icône VDM puis sur <b>Autoriser</b> dans le bandeau orange.</li>
</ol>
<p class="hint">Firefox retire les modules temporaires à sa fermeture : pour une installation permanente,
il faut la version signée par Mozilla (voir le README de VDM).</p>"""
    sections = []
    for name, steps in (("Chrome, Edge ou Brave", chromium), ("Firefox", firefox)):
        opened = browser in ("Chrome", "Edge", "Brave") if name.startswith("Chrome") else browser == "Firefox"
        tag = " <span class='tag'>page ouverte</span>" if opened else ""
        sections.append(f"<details{' open' if opened or not browser else ''}><summary>{name}{tag}</summary>{steps}</details>")
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Installer l'extension VDM</title>
<style>
:root{{--bg:#f6f8fb;--card:#fff;--fg:#1d2126;--muted:#5f6b7a;--line:#e3e8ef;--accent:#1f7ae0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181c;--card:#1f2227;--fg:#e8eaed;--muted:#9aa4b2;--line:#30343b;--accent:#5aa2f0}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 "Segoe UI",system-ui,sans-serif}}
main{{max-width:680px;margin:40px auto;padding:0 16px}}
h1{{font-size:24px;margin:0 0 6px}} p{{margin:0 0 12px}} .hint{{color:var(--muted);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:14px 0}}
.path{{display:flex;gap:8px;align-items:center}}
code{{flex:1;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:8px 10px;
      font:13px Consolas,monospace;overflow-wrap:anywhere}}
button{{font:inherit;border:0;border-radius:6px;padding:8px 14px;background:var(--accent);color:#fff;cursor:pointer}}
summary{{font-weight:600;cursor:pointer;padding:4px 0}} ol{{padding-left:20px}} li{{margin:6px 0}}
.tag{{font-size:12px;font-weight:500;background:var(--accent);color:#fff;border-radius:10px;padding:1px 8px;margin-left:6px}}
</style></head><body><main>
<h1>Installer l'extension VDM</h1>
<p>L'extension détecte les vidéos des pages et confie les téléchargements du navigateur à VDM.
Les navigateurs demandent de l'ajouter vous-même : c'est l'affaire de 3 clics, une seule fois.</p>
<div class="card"><p><b>Dossier de l'extension</b></p>
<div class="path"><code id="p">{path}</code><button id="copy">Copier</button></div>
<p class="hint">Ne supprimez pas ce dossier : le navigateur l'utilise directement.
Après une mise à jour de VDM, cliquez sur ↻ sur la carte de l'extension.</p></div>
<div class="card">{''.join(sections)}</div>
<p class="hint">VDM doit être ouvert pour que l'extension fonctionne.</p>
</main><script>
document.getElementById("copy").onclick = async (e) => {{
  try {{ await navigator.clipboard.writeText(document.getElementById("p").textContent); e.target.textContent = "Copié ✓"; }}
  catch {{ getSelection().selectAllChildren(document.getElementById("p")); e.target.textContent = "Ctrl+C"; }}
}};
</script></body></html>"""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    out = APP_DIR / "installer-extension.html"
    out.write_text(page, encoding="utf-8")
    return out


def open_setup():
    """Ouvre la page des extensions du navigateur, le dossier et le mémo. Renvoie le navigateur choisi."""
    folder = extension_dir()
    if not (folder / "manifest.json").is_file():
        raise FileNotFoundError(f"dossier de l'extension introuvable : {folder}")
    browsers = installed_browsers()
    browser = choose_browser(browsers)
    guide = write_guide(folder, browser)
    if sys.platform != "win32":
        return browser
    os.startfile(guide)   # mémo, dans le navigateur par défaut
    os.startfile(folder)  # dossier, dans l'Explorateur
    if browser:  # les pages chrome:// ou about: ne s'ouvrent qu'en lançant le navigateur lui-même
        subprocess.Popen([browsers[browser], BROWSERS[browser][1]])
    return browser

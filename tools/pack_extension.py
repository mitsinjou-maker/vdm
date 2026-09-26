"""Crée dist/vdm-extension-<version>.zip, à envoyer à Mozilla pour la signature
(ou à charger dans un navigateur).

    python tools/pack_extension.py
"""

import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "extension"
DIST = ROOT / "dist"


def main():
    version = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))["version"]
    DIST.mkdir(exist_ok=True)
    out = DIST / f"vdm-extension-{version}.zip"
    files = sorted(p for p in SRC.rglob("*") if p.is_file() and not p.name.startswith("."))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:  # manifest.json à la racine de l'archive, comme l'exige Mozilla
            z.write(f, f.relative_to(SRC).as_posix())
    print(f"{out} ({len(files)} fichiers)")


if __name__ == "__main__":
    main()

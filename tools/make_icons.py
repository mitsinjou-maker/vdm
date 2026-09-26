"""Dessine les icônes de l'extension (extension/icons/icon-<taille>.png).

    python tools/make_icons.py

Même motif que l'icône de l'application : flèche de téléchargement blanche sur un
disque bleu, avec un trait « plateau » sous la flèche pour rester lisible en 16 px.
"""

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPolygonF

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "extension" / "icons"
SIZES = (16, 32, 48, 128)


def draw(size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 128, size / 128)  # on dessine sur une grille de 128 × 128

    grad = QLinearGradient(0, 0, 0, 128)
    grad.setColorAt(0, QColor("#3b8ef0"))
    grad.setColorAt(1, QColor("#1a63c4"))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QRectF(4, 4, 120, 120))

    p.setBrush(Qt.white)
    p.drawPolygon(QPolygonF([QPointF(52, 24), QPointF(76, 24), QPointF(76, 58), QPointF(94, 58),
                             QPointF(64, 88), QPointF(34, 58), QPointF(52, 58)]))
    p.drawRoundedRect(QRectF(34, 96, 60, 10), 5, 5)
    p.end()
    return img


def main():
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 — requis par Qt
    OUT.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        path = OUT / f"icon-{size}.png"
        draw(size).save(str(path))
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()

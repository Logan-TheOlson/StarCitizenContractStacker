"""Generate assets/icon.ico — a clean stacked-crates mark in the app palette.

Run:  python assets/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ACCENT = (79, 166, 232, 255)        # #4FA6E8
ACCENT_DK = (62, 143, 204, 255)     # #3E8FCC
WIN_BG = (27, 30, 35, 255)          # #1b1e23
CARD = (35, 39, 46, 255)            # #23272e
LIGHT = (229, 233, 240, 255)        # #E5E9F0

S = 256                              # master size (downscaled into the .ico)


def _crate(d: ImageDraw.ImageDraw, cx: int, top: int, w: int, h: int,
           fill, edge) -> None:
    """A rounded 'crate' with a centre cross-brace, drawn from its top-centre."""
    x0, x1 = cx - w // 2, cx + w // 2
    y0, y1 = top, top + h
    r = h // 3
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill,
                        outline=edge, width=4)
    d.line([cx, y0 + 6, cx, y1 - 6], fill=edge, width=4)            # vertical brace
    d.line([x0 + 8, (y0 + y1) // 2, x1 - 8, (y0 + y1) // 2],
           fill=edge, width=4)                                       # horizontal brace


def build() -> Path:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # rounded tile background + accent ring
    m = 14
    d.rounded_rectangle([m, m, S - m, S - m], radius=52, fill=CARD)
    d.rounded_rectangle([m, m, S - m, S - m], radius=52, outline=ACCENT, width=8)

    # a stack of three crates: dark -> accent -> light (reads as "Cargo Stack")
    cw, ch = 150, 50
    _crate(d, S // 2, 150, cw, ch, ACCENT_DK, WIN_BG)               # bottom
    _crate(d, S // 2, 104, cw - 26, ch, ACCENT, WIN_BG)            # middle
    _crate(d, S // 2, 58, cw - 52, ch, LIGHT, WIN_BG)             # top

    out = Path(__file__).resolve().parent / "icon.ico"
    img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    return out


if __name__ == "__main__":
    print("wrote", build())

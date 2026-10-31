"""Draws the Terps WorkFlow logo (black-and-white "TW" monogram) and writes
assets/terps_workflow.ico plus logo_*.png.   Needs Pillow:  python make_icon.py"""
import math
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
N = 1024
FONT_CANDIDATES = ("Inter-BlackItalic.otf", "/usr/share/fonts/opentype/inter/Inter-BlackItalic.otf",
                   "C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/seguibl.ttf")


def _font(size):
    for f in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    raise SystemExit("No heavy italic font found - add one to FONT_CANDIDATES")


def _gray_gradient(top, bottom):
    g = Image.new("L", (N, N))
    px = g.load()
    for y in range(N):
        for x in range(N):
            t = min(1.0, max(0.0, (x * 0.35 + y * 0.94) / (N * 1.29)))
            px[x, y] = round(top + (bottom - top) * t)
    return g


def _glow(mask, radius, strength):
    return mask.filter(ImageFilter.GaussianBlur(radius)).point(lambda v: min(255, int(v * strength)))


def logo():
    tile = Image.new("L", (N, N), 0)
    ImageDraw.Draw(tile).rounded_rectangle((40, 40, N - 40, N - 40), radius=230, fill=255)
    lum = _gray_gradient(34, 6)                       # near-black tile, slightly lighter top-left
    grid = Image.new("L", (N, N), 0)                  # faint grid
    d = ImageDraw.Draw(grid)
    for i in range(0, N, 64):
        d.line((i, 0, i, N), fill=14, width=2)
        d.line((0, i, N, i), fill=14, width=2)
    lum = ImageChops.add(lum, grid)

    txt = Image.new("L", (N, N), 0)
    f = _font(470)
    d = ImageDraw.Draw(txt)
    bb = d.textbbox((0, 0), "TW", font=f)
    d.text(((N - (bb[2] - bb[0])) / 2 - bb[0] - 10, (N - (bb[3] - bb[1])) / 2 - bb[1] - 20), "TW", font=f, fill=255)
    swoosh = Image.new("L", (N, N), 0)
    ImageDraw.Draw(swoosh).arc((150, 640, 880, 900), start=200, end=340, fill=255, width=26)

    lum = ImageChops.lighter(lum, _glow(txt, 60, 0.75))      # soft white glow
    lum = ImageChops.lighter(lum, _glow(txt, 20, 0.9))
    lum = ImageChops.lighter(lum, _glow(swoosh, 18, 0.9))
    letters = _gray_gradient(255, 196)                       # white letters fading to light grey
    lum.paste(letters, (0, 0), txt)
    lum.paste(235, (0, 0), swoosh)
    edge = ImageChops.subtract(txt, txt.filter(ImageFilter.MinFilter(11)))
    lum = ImageChops.lighter(lum, edge)

    rim = Image.new("L", (N, N), 0)
    ImageDraw.Draw(rim).rounded_rectangle((42, 42, N - 42, N - 42), radius=228, outline=255, width=6)
    lum = ImageChops.lighter(lum, rim.point(lambda v: v // 5))
    return Image.merge("RGBA", (lum, lum, lum, tile))


def main():
    os.makedirs(OUT, exist_ok=True)
    big = logo()
    sizes = [16, 20, 24, 32, 40, 48, 64, 80, 128, 256]
    imgs = [big.resize((s, s), Image.LANCZOS) for s in sizes]
    imgs[-1].save(os.path.join(OUT, "terps_workflow.ico"), sizes=[(s, s) for s in sizes], append_images=imgs[:-1])
    for s in (40, 64, 80, 256):
        big.resize((s, s), Image.LANCZOS).save(os.path.join(OUT, f"logo_{s}.png"))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

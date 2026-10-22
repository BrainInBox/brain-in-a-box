"""Draws the installers' pictures and the site's link preview, from Bibo's icon and the site's fonts.

    python3 -I app/src-tauri/installer/images.py      # needs Pillow; tiffutil (macOS) for the DMG

- nsis-sidebar.bmp (164x314) and nsis-header.bmp (150x57): the Windows installer's first and last
  pages, and the header of the others (24-bit BMP, what NSIS loads).
- dmg-background.tiff: the Mac disk image's window (660x400, and twice that for Retina), with the
  arrow from Bibo to Applications at the places tauri.macos.conf.json gives them.
- site/img/og.jpg (1200x630): the picture a shared link shows (WhatsApp, Messenger, mail).
"""
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BIBO = Image.open(ROOT / "app" / "src-tauri" / "icons" / "icon.png").convert("RGBA")
FONTS = ROOT / "site" / "fonts"

CREAM = (251, 245, 236)        # the app's --bg
INK = (46, 34, 32)             # the app's --ink
INK_2 = (107, 90, 85)          # the app's --ink-2
ACCENT = (194, 58, 110)        # the app's --accent
NIGHT = (10, 10, 10)           # the site's --bg
PINK = (247, 169, 196)         # the site's --pink


def font(name, size, weight):
    f = ImageFont.truetype(str(FONTS / name), size)
    f.set_variation_by_axes([weight])
    return f


def head(size, weight=650):
    return font("inter-tight-latin-wght-normal.woff2", size, weight)


def text(size, weight=450):
    return font("inter-latin-wght-normal.woff2", size, weight)


def bibo(size):
    return BIBO.resize((size, size), Image.LANCZOS)


def centered(draw, y, line, f, fill, width):
    w = draw.textlength(line, font=f)
    draw.text(((width - w) / 2, y), line, font=f, fill=fill)


def nsis_sidebar(scale=1):
    w, h = 164 * scale, 314 * scale
    im = Image.new("RGBA", (w, h), CREAM)
    b = bibo(150 * scale)
    im.alpha_composite(b, ((w - b.width) // 2, 52 * scale))
    d = ImageDraw.Draw(im)
    centered(d, 206 * scale, "Bibo", head(30 * scale, 700), INK, w)
    centered(d, 244 * scale, "Brain in a Box", text(13 * scale, 500), INK_2, w)
    return im.convert("RGB")


def nsis_header():
    im = Image.new("RGBA", (150, 57), (255, 255, 255, 255))
    b = bibo(52)
    im.alpha_composite(b, (150 - 52 - 4, 3))
    return im.convert("RGB")


def arrow(d, x0, x1, y, s, color):
    d.line([(x0, y), (x1 - 14 * s, y)], fill=color, width=6 * s)
    d.polygon([(x1, y), (x1 - 22 * s, y - 14 * s), (x1 - 22 * s, y + 14 * s)], fill=color)


def dmg_background(s):
    # The places of tauri.macos.conf.json: Bibo at (180, 170), Applications at (480, 170), 128 px icons.
    w, h = 660 * s, 400 * s
    im = Image.new("RGB", (w, h), CREAM)
    d = ImageDraw.Draw(im)
    centered(d, 34 * s, "Glisse Bibo dans Applications", head(30 * s, 700), INK, w)
    arrow(d, 262 * s, 398 * s, 170 * s, s, ACCENT)
    centered(d, 318 * s, "Puis ouvre Brain in a Box depuis le dossier Applications.", text(15 * s, 450), INK_2, w)
    return im


def og_image():
    w, h = 1200, 630
    im = Image.new("RGBA", (w, h), NIGHT + (255,))
    b = bibo(360)
    im.alpha_composite(b, (w - 360 - 70, (h - 360) // 2 + 10))
    d = ImageDraw.Draw(im)
    d.text((80, 78), "Brain in a Box", font=head(30, 650), fill=PINK)
    y = 150
    for line in ("Claude te guide", "pas à pas dans", "tes logiciels."):
        d.text((80, y), line, font=head(76, 700), fill=(250, 250, 250))
        y += 88
    d.text((80, y + 30), "Bibo installe tout, sans rien taper.", font=text(30, 450), fill=(163, 163, 163))
    d.text((80, y + 74), "L’app est gratuite, avec ton propre abonnement Claude.", font=text(26, 450), fill=(163, 163, 163))
    return im.convert("RGB")


def main():
    nsis_sidebar().save(HERE / "nsis-sidebar.bmp")
    nsis_header().save(HERE / "nsis-header.bmp")
    with tempfile.TemporaryDirectory() as tmp:
        one, two = Path(tmp) / "bg.png", Path(tmp) / "bg@2x.png"
        dmg_background(1).save(one, dpi=(72, 72))
        dmg_background(2).save(two, dpi=(144, 144))
        # One TIFF holding both: Finder shows the sharp one on a Retina screen.
        subprocess.run(["tiffutil", "-cathidpicheck", str(one), str(two), "-out", str(HERE / "dmg-background.tiff")],
                       check=True, capture_output=True)
    og_image().save(ROOT / "site" / "img" / "og.jpg", quality=86, optimize=True, progressive=True)
    for p in sorted(HERE.glob("*.bmp")) + [HERE / "dmg-background.tiff", ROOT / "site" / "img" / "og.jpg"]:
        print(p.relative_to(ROOT), p.stat().st_size, "bytes")


if __name__ == "__main__":
    sys.exit(main())

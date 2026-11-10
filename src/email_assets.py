"""
Lead Generation — Email images
Logo, client logos and portfolio photos, resized and converted for email.

Drop files into these folders; they are resized automatically on send:
    templates/assets/logo.png     logo with white lettering, transparent, for the black header
    templates/assets/clients/     client logos   (png, jpg, webp)
    templates/assets/portfolio/   project photos (png, jpg, webp)

Images go inside each email as inline attachments (src="cid:..."), so they
show without hosting them anywhere. WebP is converted to PNG/JPEG because
Outlook for Windows can't display it.
"""

import io
import os
import base64
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = Path(os.getenv("EMAIL_ASSETS_DIR", BASE_DIR / "templates" / "assets"))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Pixel sizes are ~2x the displayed size so images stay sharp on phones
LOGO_WIDTH = 400            # shown at 200px
CLIENT_TILE = (216, 224)    # shown at 106x110, 5 per row (2 on phones): black rounded card
CLIENT_PADDING = (20, 28)
CARD_RADIUS = 20
CARD_FILL = (17, 17, 17, 255)
CARD_OUTLINE = (58, 58, 58, 255)
LIGHTEN_BELOW = 95          # on black cards, logo pixels darker than this (luminance) turn white
PORTFOLIO_SIZE = (560, 420) # shown at 264x198, cropped to 4:3
MAX_PORTFOLIO = 6

# Mail apps in dark mode recolor the email's backgrounds and text but never its images.
# So the client logos sit on black cards that are part of the image, and their dark
# lettering is turned white so it reads on black in every mode.


@dataclass
class EmailImage:
    cid: str
    data: bytes
    subtype: str    # png | jpeg
    alt: str

    @property
    def filename(self) -> str:
        return f"{self.cid}.{'jpg' if self.subtype == 'jpeg' else 'png'}"


def _png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _jpeg(im: Image.Image, quality: int = 78) -> bytes:
    if im.mode != "RGB":
        background = Image.new("RGB", im.size, "white")
        background.paste(im, mask=im.convert("RGBA").split()[-1])
        im = background
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _alt_from_filename(path: Path) -> str:
    name = re.sub(r"^\d+[-_ ]", "", path.stem)          # "01-tower" -> "tower"
    return re.sub(r"[-_]+", " ", name).strip().title()


def _image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_EXTS)


def _lighten_dark_parts(im: Image.Image) -> Image.Image:
    """Turn dark pixels white (keeps alpha and bright/colored parts), for logos on black.

    Only for logos with a transparent background: a logo with its own solid background
    (e.g. a dark square with light text) is already readable and is left alone.
    """
    alpha = im.getchannel("A")
    if alpha.getextrema()[0] == 255:            # fully opaque: has its own background
        return im
    px = im.load()
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = px[x, y]
            if a and 0.299 * r + 0.587 * g + 0.114 * b < LIGHTEN_BELOW:
                px[x, y] = (255, 255, 255, a)
    return im


def _card(path: Path, size: tuple, padding: tuple) -> bytes:
    """The image at path, fitted inside padding and centered on a black rounded card."""
    w, h = size
    logo = Image.open(path).convert("RGBA")
    logo.thumbnail((w - 2 * padding[0], h - 2 * padding[1]), Image.LANCZOS)
    logo = _lighten_dark_parts(logo)
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, w - 1, h - 1), CARD_RADIUS, fill=CARD_FILL, outline=CARD_OUTLINE)
    card.alpha_composite(logo, ((w - logo.width) // 2, (h - logo.height) // 2))
    return _png(card)


def _logo() -> EmailImage | None:
    """NEST logo with white lettering (transparent background), for the black header."""
    path = ASSETS_DIR / "logo.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGBA")
    im.thumbnail((LOGO_WIDTH, LOGO_WIDTH), Image.LANCZOS)
    return EmailImage("logo", _png(im), "png", "NEST")


def _client(path: Path, index: int) -> EmailImage:
    """Client logo on a black card, readable on light and dark backgrounds."""
    return EmailImage(f"client{index}", _card(path, CLIENT_TILE, CLIENT_PADDING), "png",
                      _alt_from_filename(path))


def _portfolio(path: Path, index: int) -> EmailImage:
    im = ImageOps.exif_transpose(Image.open(path))      # respect phone rotation
    im = ImageOps.fit(im.convert("RGB"), PORTFOLIO_SIZE, Image.LANCZOS)
    return EmailImage(f"work{index}", _jpeg(im), "jpeg", _alt_from_filename(path))


@lru_cache(maxsize=1)
def load_email_images() -> dict:
    """{"logo": EmailImage|None, "clients": [...], "portfolio": [...]} — built once per run."""
    return {
        "logo": _logo(),
        "clients": [_client(p, i) for i, p in enumerate(_image_files(ASSETS_DIR / "clients"))],
        "portfolio": [_portfolio(p, i) for i, p in
                      enumerate(_image_files(ASSETS_DIR / "portfolio")[:MAX_PORTFOLIO])],
    }


def all_images(images: dict) -> list[EmailImage]:
    """Flat list of every image, for attaching to an email."""
    return ([images["logo"]] if images["logo"] else []) + images["clients"] + images["portfolio"]


def template_images(images: dict, preview: bool = False) -> dict:
    """Template variables for the images.

    Sending: src is "cid:<id>" (matches the inline attachment).
    Preview: src is a data: URI, so the preview file opens anywhere on its own.
    (Never send data: URIs — Gmail blocks them.)
    """
    def src(img: EmailImage) -> str:
        if not preview:
            return f"cid:{img.cid}"
        return f"data:image/{img.subtype};base64,{base64.b64encode(img.data).decode()}"

    return {
        "logo_src": src(images["logo"]) if images["logo"] else "",
        "clients": [{"src": src(c), "alt": c.alt} for c in images["clients"]],
        "portfolio": [{"src": src(p), "alt": p.alt} for p in images["portfolio"]],
    }

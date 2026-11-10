"""
Lead Generation — Email images
Logo, client logos and portfolio photos, resized and converted for email.

Drop files into these folders; they are resized automatically on send:
    templates/assets/logo-black.png  logo (black lettering), shown on a white card in the black header
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
LOGO_CARD = (440, 220)      # shown at 220x110: white rounded card with the logo centered
LOGO_PADDING = (44, 26)
CLIENT_TILE = (540, 200)    # white rounded card with the logo centered (5 per row, 2 on phones)
CLIENT_PADDING = (48, 30)
CARD_RADIUS = 28
PORTFOLIO_SIZE = (560, 420) # shown at 264x198, cropped to 4:3
MAX_PORTFOLIO = 6

# Mail apps in dark mode recolor the email's backgrounds and text but never its images.
# So anything that must stay readable on a dark background is baked into the image:
# the NEST logo and the client logos each sit on a white card that is part of the image.


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


def _card(path: Path, size: tuple, padding: tuple) -> bytes:
    """The image at path, fitted inside padding and centered on a white rounded card."""
    w, h = size
    logo = Image.open(path).convert("RGBA")
    logo.thumbnail((w - 2 * padding[0], h - 2 * padding[1]), Image.LANCZOS)
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(
        (0, 0, w - 1, h - 1), CARD_RADIUS, fill=(255, 255, 255, 255), outline=(226, 226, 222, 255))
    card.alpha_composite(logo, ((w - logo.width) // 2, (h - logo.height) // 2))
    return _png(card)


def _logo() -> EmailImage | None:
    """NEST logo (black lettering) on a white card, for the black header."""
    path = ASSETS_DIR / "logo-black.png"
    if not path.exists():
        return None
    return EmailImage("logo", _card(path, LOGO_CARD, LOGO_PADDING), "png", "NEST")


def _client(path: Path, index: int) -> EmailImage:
    """Client logo on a white card, readable on light and dark backgrounds."""
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

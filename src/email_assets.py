"""
Lead Generation — Email images
Logo, client logos and portfolio photos, resized and converted for email.

Drop files into these folders; they are resized automatically on send:
    templates/assets/logo.png
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

from PIL import Image, ImageOps

BASE_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = Path(os.getenv("EMAIL_ASSETS_DIR", BASE_DIR / "templates" / "assets"))
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# Pixel sizes are ~2x the displayed size so images stay sharp on phones
LOGO_WIDTH = 360            # shown at 180px
CLIENT_BOX = (200, 80)      # shown within 100x40
PORTFOLIO_SIZE = (560, 420) # shown at 264x198, cropped to 4:3
MAX_PORTFOLIO = 6


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


def _logo() -> EmailImage | None:
    path = ASSETS_DIR / "logo.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGBA")
    im.thumbnail((LOGO_WIDTH, LOGO_WIDTH), Image.LANCZOS)
    return EmailImage("logo", _png(im), "png", "NEST")


def _client(path: Path, index: int) -> EmailImage:
    im = Image.open(path).convert("RGBA")
    im.thumbnail(CLIENT_BOX, Image.LANCZOS)
    return EmailImage(f"client{index}", _png(im), "png", _alt_from_filename(path))


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

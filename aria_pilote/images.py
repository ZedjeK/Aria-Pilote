"""Images jointes aux messages : rangées à côté de la conversation (data/conversations/<id>/), réduites pour
ménager le contexte, puis envoyées au moteur local quand le modèle chargé a son projecteur vision (mmproj).

Le message garde seulement leurs noms et tailles : {"file": "3f2a….png", "w": 1280, "h": 720}.
"""

import base64
import binascii
import io
import math
import re
import shutil
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from . import conversations

MAX_PER_MESSAGE = 5
MAX_BYTES = 20 * 1024 * 1024  # avant réduction
MAX_SIDE = 1280  # côté le plus long envoyé au modèle : une capture 2560x1440 garde un texte lisible
PATCH = 32  # un token d'image couvre environ 32x32 pixels (Qwen-VL) : sert seulement à estimer le contexte
FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "png", "GIF": "png", "BMP": "png"}
NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg)$")
DATA_URL_RE = re.compile(r"^data:image/[a-z0-9.+-]+;base64,(.*)$", re.DOTALL)


class ImageError(Exception):
    pass


def folder(cid):
    return conversations.DATA_DIR / conversations._path(cid).stem


def path(cid, name):
    if not NAME_RE.match(name or ""):
        raise ImageError("Image introuvable.")
    target = folder(cid) / name
    if not target.is_file():
        raise ImageError("Image introuvable.")
    return target


def save(cid, data_url):
    """Image collée ou jointe (data URL) -> fichier réduit dans le dossier de la conversation."""
    match = DATA_URL_RE.match(data_url or "")
    if not match:
        raise ImageError("Image illisible : seules les images (PNG, JPEG, WebP, GIF, BMP) sont acceptées.")
    try:
        raw = base64.b64decode(match.group(1), validate=True)
    except (binascii.Error, ValueError):
        raise ImageError("Image illisible : données corrompues.")
    if len(raw) > MAX_BYTES:
        raise ImageError(f"Image trop lourde : {MAX_BYTES // (1024 * 1024)} Mo au plus.")
    try:
        img = Image.open(io.BytesIO(raw))
        fmt = img.format
        img = ImageOps.exif_transpose(img)  # photo de téléphone : la bonne orientation
        img.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ImageError("Image illisible : format inconnu ou fichier abîmé.")
    if fmt not in FORMATS:
        raise ImageError("Format non pris en charge : PNG, JPEG, WebP, GIF ou BMP.")
    img.thumbnail((MAX_SIDE, MAX_SIDE))  # ne fait que réduire, en gardant les proportions
    ext = FORMATS[fmt]
    # PNG pour les captures (le texte reste net), JPEG pour les photos
    img = img.convert("RGB") if ext == "jpg" or img.mode not in ("RGB", "RGBA", "L") else img
    name = f"{uuid.uuid4().hex}.{ext}"
    target = folder(cid)
    target.mkdir(parents=True, exist_ok=True)
    img.save(target / name, **({"quality": 90} if ext == "jpg" else {"optimize": True}))
    return {"file": name, "w": img.width, "h": img.height}


def data_url(cid, name):
    target = path(cid, name)
    mime = "image/jpeg" if target.suffix == ".jpg" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(target.read_bytes()).decode()}"


def tokens(image):
    """Estimation du coût d'une image dans le contexte (le compte exact dépend du projecteur du modèle)."""
    return math.ceil(image["w"] / PATCH) * math.ceil(image["h"] / PATCH)


def size_from_url(url):
    """Largeur et hauteur d'une image en data URL, sans la décoder entièrement."""
    match = DATA_URL_RE.match(url or "")
    try:
        with Image.open(io.BytesIO(base64.b64decode(match.group(1)))) as img:
            return {"w": img.width, "h": img.height}
    except (AttributeError, binascii.Error, ValueError, OSError, UnidentifiedImageError):
        return {"w": MAX_SIDE, "h": MAX_SIDE}


def remove(cid):
    shutil.rmtree(folder(cid), ignore_errors=True)

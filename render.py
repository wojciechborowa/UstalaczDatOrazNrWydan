"""Zamiana plikow wejsciowych na obrazy JPEG w base64 (bez OCR - czysta rasteryzacja)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from config import IMG_EXT, JPEG_QUALITY, MAX_IMAGE_DIM, PDF_EXT, RENDER_DPI

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

Image.MAX_IMAGE_PIXELS = None  # duze skany gazet potrafia przekroczyc domyslny limit


def kind_of(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in PDF_EXT:
        return "pdf"
    if ext in IMG_EXT:
        return "image"
    return "other"


def _fit(img: Image.Image, max_dim: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / float(max(w, h))
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)


def load_first_page(path: str | Path, dpi: int = RENDER_DPI) -> Image.Image:
    """Pierwsza strona PDF-a lub caly obraz, jako PIL.Image w RGB."""
    p = Path(path)
    k = kind_of(p)
    if k == "pdf":
        if fitz is None:
            raise RuntimeError("Brak biblioteki PyMuPDF - zainstaluj: pip install PyMuPDF")
        doc = fitz.open(str(p))
        try:
            if doc.page_count == 0:
                raise RuntimeError("PDF nie zawiera stron")
            pix = doc[0].get_pixmap(dpi=dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return img.convert("RGB")
        finally:
            doc.close()
    elif k == "image":
        img = Image.open(str(p))
        return img.convert("RGB")
    raise RuntimeError(f"Nieobslugiwany format pliku: {p.suffix}")


def to_data_url(path: str | Path, max_dim: int = MAX_IMAGE_DIM, dpi: int = RENDER_DPI) -> str:
    """Gotowy data-URL do wyslania w polu image_url."""
    img = _fit(load_first_page(path, dpi=dpi), max_dim)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def thumbnail(path: str | Path, width: int = 300, top_fraction: float = 0.45) -> Image.Image:
    """Gorny pasek strony - tam siedzi winieta z data i numerem.

    Pozwala zweryfikowac wynik wzrokiem bez otwierania pliku.
    """
    img = load_first_page(path, dpi=90)
    w, h = img.size
    img = img.crop((0, 0, w, max(1, int(h * top_fraction))))
    scale = width / float(img.size[0])
    return img.resize((width, max(1, int(img.size[1] * scale))), Image.LANCZOS)

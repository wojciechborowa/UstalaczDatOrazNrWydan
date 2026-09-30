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


def page_count(path: str | Path) -> int:
    """Liczba stron: dla PDF-a z dokumentu, dla obrazu zawsze 1."""
    p = Path(path)
    if kind_of(p) != "pdf":
        return 1
    if fitz is None:
        raise RuntimeError("Brak biblioteki PyMuPDF - zainstaluj: pip install PyMuPDF")
    doc = fitz.open(str(p))
    try:
        return doc.page_count
    finally:
        doc.close()


def load_first_page(path: str | Path, dpi: int = RENDER_DPI) -> Image.Image:
    """Pierwsza strona PDF-a lub caly obraz, jako PIL.Image w RGB."""
    return load_page(path, 0, dpi=dpi)


def load_page(path: str | Path, index: int = 0, dpi: int = RENDER_DPI) -> Image.Image:
    """Strona o numerze `index` (od 0) z PDF-a lub caly obraz, jako PIL.Image w RGB."""
    p = Path(path)
    k = kind_of(p)
    if k == "pdf":
        if fitz is None:
            raise RuntimeError("Brak biblioteki PyMuPDF - zainstaluj: pip install PyMuPDF")
        doc = fitz.open(str(p))
        try:
            if doc.page_count == 0:
                raise RuntimeError("PDF nie zawiera stron")
            if not 0 <= index < doc.page_count:
                raise RuntimeError(f"PDF nie ma strony {index + 1}")
            pix = doc[index].get_pixmap(dpi=dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return img.convert("RGB")
        finally:
            doc.close()
    elif k == "image":
        img = Image.open(str(p))
        return img.convert("RGB")
    raise RuntimeError(f"Nieobslugiwany format pliku: {p.suffix}")


def _font(size: int):
    """Czytelna czcionka do paska z identyfikatorem - systemowa albo wbudowana."""
    from PIL import ImageFont
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSansMono-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/Library/Fonts/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def stamp_id(img: Image.Image, ident: str) -> Image.Image:
    """Dokleja nad obrazem bialy pasek z napisem "### 00042 ###".

    Model przepisuje ten identyfikator do odpowiedzi, wiec wynik da sie przypisac
    do wlasciwego pliku nawet wtedy, gdy model pomiesza kolejnosc obrazow w paczce.
    """
    from PIL import ImageDraw
    w, h = img.size
    bar = max(48, w // 14)
    out = Image.new("RGB", (w, h + bar), "white")
    out.paste(img, (0, bar))
    draw = ImageDraw.Draw(out)
    text = f"### {ident} ###"
    font = _font(int(bar * 0.62))
    try:
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        tw, th = x1 - x0, y1 - y0
    except Exception:
        tw, th, x0, y0 = len(text) * bar // 3, bar // 2, 0, 0
    draw.text(((w - tw) // 2 - x0, (bar - th) // 2 - y0), text, fill="black", font=font)
    draw.line((0, bar - 1, w, bar - 1), fill="black", width=2)
    return out


DETAIL_DPI = 240
DETAIL_TOP = 0.35      # jaka czesc strony (od gory) powiekszamy w trybie dokladnym


def load_page_for_ai(path: str | Path, page: int = 0, dpi: int = RENDER_DPI) -> Image.Image:
    """Strona wysylana do AI. Obraz ma zawsze jedna strone; PDF bez tej strony = blad
    z czytelnym komunikatem (bez cichego brania innej strony)."""
    if kind_of(path) == "pdf" and page > 0:
        n = page_count(path)
        if page >= n:
            raise RuntimeError(f"PDF ma {n} str., a w ustawieniach sesji wybrano strone {page + 1}")
        return load_page(path, page, dpi=dpi)
    return load_page(path, 0, dpi=dpi)


def detail_image(path: str | Path, width: int = 1600, page: int = 0) -> Image.Image:
    """Obraz do dokladnego odczytu: powiekszony gorny pas strony (winieta z data
    i numerem), pod nim cala strona. Model widzi szczegoly i kontekst naraz."""
    img = load_page_for_ai(path, page, dpi=DETAIL_DPI)
    w, h = img.size
    top = img.crop((0, 0, w, max(1, int(h * DETAIL_TOP))))
    top = top.resize((width, max(1, int(top.size[1] * width / w))), Image.LANCZOS)
    full = _fit(img, max(width, 1400))
    if full.size[0] > width:
        full = full.resize((width, int(full.size[1] * width / full.size[0])), Image.LANCZOS)
    gap = 12
    out = Image.new("RGB", (width, top.size[1] + gap + full.size[1]), (150, 150, 150))
    out.paste(top, (0, 0))
    out.paste(full, ((width - full.size[0]) // 2, top.size[1] + gap))
    return out


def to_jpeg_b64(path: str | Path, ident: str | None = None,
                max_dim: int = MAX_IMAGE_DIM, dpi: int = RENDER_DPI,
                detail: bool = False, page: int = 0) -> str:
    """Strona (domyslnie pierwsza) jako JPEG w base64, opcjonalnie z paskiem identyfikatora."""
    img = detail_image(path, page=page) if detail else _fit(load_page_for_ai(path, page, dpi=dpi), max_dim)
    if ident:
        img = stamp_id(img, ident)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def to_data_url(path: str | Path, max_dim: int = MAX_IMAGE_DIM, dpi: int = RENDER_DPI) -> str:
    """Gotowy data-URL do wyslania w polu image_url."""
    return "data:image/jpeg;base64," + to_jpeg_b64(path, None, max_dim, dpi)


def thumbnail(path: str | Path, width: int = 300, top_fraction: float = 0.45,
              page: int = 0) -> Image.Image:
    """Gorny pasek strony - tam siedzi winieta z data i numerem.

    Pozwala zweryfikowac wynik wzrokiem bez otwierania pliku.
    """
    try:
        img = load_page_for_ai(path, page, dpi=90)
    except RuntimeError:
        img = load_first_page(path, dpi=90)
    w, h = img.size
    img = img.crop((0, 0, w, max(1, int(h * top_fraction))))
    scale = width / float(img.size[0])
    return img.resize((width, max(1, int(img.size[1] * scale))), Image.LANCZOS)

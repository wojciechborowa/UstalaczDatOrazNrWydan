"""Stale konfiguracyjne i sciezki aplikacji."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "Czytnik wydan AI"
APP_VERSION = "1.0"

APP_DIR = Path.home() / ".gazeta_ai"
CONFIG_FILE = APP_DIR / "config.json"
CACHE_DB = APP_DIR / "cache.sqlite"
RENAME_LOG_DIR = APP_DIR / "renames"
SESSION_EXT = ".gsess"

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
REFERER = "https://localhost/gazeta-ai"
X_TITLE = "Gazeta AI Reader"

# Limity darmowego planu OpenRouter (stan wg dokumentacji /docs/api_reference/limits)
FREE_RPM = 20                  # zapytan na minute
FREE_RPD_NO_CREDITS = 50       # zapytan dziennie bez doladowania
FREE_RPD_WITH_CREDITS = 1000   # zapytan dziennie po zakupie >=10 kredytow

BATCH_SIZE = 5                 # ile skanow w jednym zapytaniu
RENDER_DPI = 180               # rasteryzacja pierwszej strony PDF
MAX_IMAGE_DIM = 1600           # dluzszy bok wysylanego obrazu (px)
JPEG_QUALITY = 85
VERIFY_CONFIDENCE = 0.8        # ponizej tej pewnosci rekord trafia do weryfikacji
VERIFY_DPI = 150               # rozdzielczosc podgladu w oknie weryfikacji
MAX_RETRIES = 5
REQUEST_TIMEOUT = 180

PDF_EXT = {".pdf"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
ALL_EXT = PDF_EXT | IMG_EXT

# Kolumny tabeli: (klucz, naglowek, szerokosc)
COLUMNS = [
    ("check", "", 34),
    ("old_name", "Stara nazwa", 250),
    ("title", "Tytul", 130),
    ("date_iso", "Data", 95),
    ("issue", "Nr wydania", 95),
    ("page", "Str.", 50),
    ("new_name", "Nowa nazwa", 300),
    ("confidence", "Pewnosc", 70),
    ("status", "Status", 120),
    ("note", "Uwagi", 200),
]

# Kolumny eksportu (wszystkie dane, jakie program przechowuje)
EXPORT_COLUMNS = [
    ("old_name", "Stara nazwa"),
    ("path", "Sciezka"),
    ("kind", "Typ"),
    ("title", "Tytul"),
    ("language", "Jezyk"),
    ("date_iso", "Data ISO"),
    ("date_raw", "Data (oryginal)"),
    ("month_raw", "Miesiac (oryginal)"),
    ("year_printed", "Rok nadrukowany"),
    ("issue_number", "Nr wydania"),
    ("issue_suffix", "Dopisek"),
    ("page_number", "Nr strony"),
    ("is_cover", "Strona tytulowa"),
    ("confidence", "Pewnosc"),
    ("new_name", "Nowa nazwa"),
    ("status", "Status"),
    ("note", "Uwagi"),
    ("model", "Model"),
    ("raw", "Surowa odpowiedz"),
]


def ensure_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    RENAME_LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    """Zapis klucza API i ustawien. Plik dostaje prawa 600 (tylko wlasciciel)."""
    ensure_dirs()
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except Exception:
        pass

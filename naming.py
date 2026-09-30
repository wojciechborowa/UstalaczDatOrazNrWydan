"""Budowa nazw plikow wyjsciowych.

Format:
    "France Football - 1958-06-17 - 000638.pdf"
    "France Football - 1958-06-17 - 000638 - 015.jpg"   (pojedyncza strona)
    "Placar - 1976-mm-dd - 000123.jpg"                  (nieznany dzien i miesiac)
    "0001 - O Fluminense (RJ) - 1954-07-20 - 022025 - 006-OST.jpg"
                                                        (kolekcja - zmienia sie tylko data)

Numer wydania: liczba dopelniona zerami do 6 cyfr, dopisek po mysliniku ("000315-bis").
Numer strony:  dopelniony zerami do 3 cyfr.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from pathlib import Path

ILLEGAL = r'<>:"/\\|?*'
_MULTISPACE = re.compile(r"\s+")

ISSUE_PAD = 6
PAGE_PAD = 3


def clean_title(title: str | None) -> str:
    if not title:
        return ""
    t = str(title).strip()
    # model czasem oddaje winiete wersalikami - sprowadz do normalnej pisowni
    letters = [c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        t = t.title()
    t = "".join(ch for ch in t if ch not in ILLEGAL)
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C")
    t = _MULTISPACE.sub(" ", t).strip(" .-")
    return t


def clean_suffix(suffix: str | None) -> str:
    if not suffix:
        return ""
    s = str(suffix).strip().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def format_issue(number: str | None, suffix: str | None) -> str:
    """"638" -> "000638";  "315"+"bis" -> "000315-bis"."""
    if number is None or str(number).strip() == "":
        return ""
    digits = re.sub(r"\D", "", str(number))
    if not digits:
        return ""
    core = digits.zfill(ISSUE_PAD)
    suf = clean_suffix(suffix)
    return f"{core}-{suf}" if suf else core


def format_page(page: str | None) -> str:
    if page is None or str(page).strip() == "":
        return ""
    digits = re.sub(r"\D", "", str(page))
    if not digits:
        return ""
    return digits.zfill(PAGE_PAD)


def normalize_date(text: str | None) -> str | None:
    """Data wpisana recznie -> RRRR-MM-DD.

    Przyjmuje RRRR-MM-DD, DD.MM.RRRR, DD-MM-RRRR, DD/MM/RRRR i DDMMRRRR.
    Zwraca None, gdy tekstu nie da sie zrozumiec.
    """
    t = (text or "").strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", t)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.fullmatch(r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", t) or \
            re.fullmatch(r"(\d{2})(\d{2})(\d{4})", t)
        if not m:
            return None
        d, mo, y = m.groups()
    iso = f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return iso if is_valid_date(iso) else None


def is_valid_date(date_iso: str | None) -> bool:
    if not date_iso:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_iso).strip()):
        return False
    y, m, d = (int(x) for x in str(date_iso).split("-"))
    if not 1800 <= y <= 2100:
        return False
    try:
        _dt.date(y, m, d)      # 31 kwietnia albo 29 lutego w zwyklym roku - nie istnieje
    except ValueError:
        return False
    return True


UNKNOWN_TITLE = "Nieznany tytul"


def build_new_name(rec: dict, title_override: str = "") -> tuple[str, str]:
    """Zwraca (nazwa, braki). Nazwa jest zawsze w formacie docelowym - czego nie wiadomo,
    to zostaje znacznikiem (rrrr, mm, dd, nnnnnn). Pusty tekst brakow == nazwa kompletna.

    Pliki z kolekcji (wzorzec z lp) zachowuja nazwe wejsciowa - podmieniana jest tylko data.
    """
    src = Path(rec.get("path", "") or rec.get("old_name", ""))
    nd = rec.get("name_data") or {}
    date_iso = (rec.get("date_iso") or "").strip()
    date_ok = is_valid_date(date_iso)

    if nd.get("keep_name") and nd.get("date_span"):
        stem, ext = src.stem, src.suffix
        s, e = nd["date_span"]
        if date_ok:
            return f"{stem[:s]}{date_iso}{stem[e:]}{ext}", ""
        year = nd.get("year")
        return f"{stem[:s]}{year or 'rrrr'}-mm-dd{stem[e:]}{ext}", "brak: data"

    ext = src.suffix.lower() or ".pdf"
    missing = []
    title = clean_title(title_override or rec.get("title") or nd.get("title"))
    if not title:
        title = UNKNOWN_TITLE
        missing.append("tytul")
    if date_ok:
        date = date_iso
    else:
        year = nd.get("year")
        if not year and date_iso[:4].isdigit():
            year = int(date_iso[:4])
        date = f"{year or 'rrrr'}-mm-dd"
        missing.append("data")
    issue = format_issue(rec.get("issue_number"), rec.get("issue_suffix"))
    if not issue:
        issue = "n" * ISSUE_PAD
        missing.append("nr wydania")
    page = format_page(rec.get("page_number"))
    if page and nd.get("ost"):
        page += "-OST"
    lp = f"{nd['lp']} - " if nd.get("lp") else ""
    name = f"{lp}{title} - {date} - {issue}" + (f" - {page}" if page else "") + ext
    return name, ("brak: " + ", ".join(missing)) if missing else ""


def resolve_collision(target: Path, taken: set[str]) -> Path:
    """Dokleja ' (2)', ' (3)'... gdy nazwa juz istnieje na dysku lub w tej partii."""
    key = str(target).lower()
    if key not in taken and not target.exists():
        return target
    stem, ext, parent = target.stem, target.suffix, target.parent
    i = 2
    while True:
        cand = parent / f"{stem} ({i}){ext}"
        if str(cand).lower() not in taken and not cand.exists():
            return cand
        i += 1

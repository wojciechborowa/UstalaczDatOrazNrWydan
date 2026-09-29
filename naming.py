"""Budowa nazw plikow wyjsciowych.

Format:
    "France Football - 1958-06-17 - 000638.pdf"
    "France Football - 1958-06-17 - 000638 - 015.jpg"   (pojedyncza strona)

Numer wydania: liczba dopelniona zerami do 6 cyfr, dopisek po mysliniku ("000315-bis").
Numer strony:  dopelniony zerami do 3 cyfr.
"""
from __future__ import annotations

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


def is_valid_date(date_iso: str | None) -> bool:
    if not date_iso:
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date_iso).strip()):
        return False
    y, m, d = (int(x) for x in str(date_iso).split("-"))
    return 1800 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31


def build_new_name(rec: dict, title_override: str = "") -> tuple[str, str]:
    """Zwraca (nazwa, powod_bledu). Pusta nazwa == nie da sie zbudowac."""
    ext = Path(rec.get("path", "")).suffix.lower() or ".pdf"
    kind = rec.get("kind", "pdf")

    title = clean_title(title_override or rec.get("title"))
    date_iso = (rec.get("date_iso") or "").strip()
    issue = format_issue(rec.get("issue_number"), rec.get("issue_suffix"))

    missing = []
    if not title:
        missing.append("tytul")
    if not is_valid_date(date_iso):
        missing.append("data")
    if not issue:
        missing.append("nr wydania")

    if kind == "image":
        page = format_page(rec.get("page_number"))
        if not page:
            missing.append("nr strony")
    else:
        page = ""

    if missing:
        return "", "brak: " + ", ".join(missing)

    if page:
        return f"{title} - {date_iso} - {issue} - {page}{ext}", ""
    return f"{title} - {date_iso} - {issue}{ext}", ""


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

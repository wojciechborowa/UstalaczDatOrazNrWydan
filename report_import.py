"""Import raportow CSV z programu "gazety" (raport_dat.csv i podobne).

Raport zawiera ustalone tam daty i numery wydan. Rekordy naszej sesji dopasowujemy
do wierszy raportu po kolei:
  1. skrot pliku (kolumna `skrot`, format "h2:<md5>") - dziala mimo zmiany nazwy
     i przeniesienia pliku,
  2. pelna sciezka,
  3. nazwa pliku + rozmiar.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from pathlib import Path

import naming

# Musi byc identyczne z gazety_core.file_hash - inaczej zaden skrot nie bedzie pasowal.
HASH_HEAD = 256 * 1024
HASH_TAIL = 256 * 1024
HASH_MARK = "h2"

STATUS_OK = "pewne"
REQUIRED = ("data_koncowa",)

RE_SUFFIX = re.compile(r"\d{2,6}[ -]+(bis|special|ter)\b", re.I)
RE_TITLE = re.compile(r"^(.+?) - \d{4}-\d{2}-\d{2} - \d")


def file_hash_h2(path: str | Path) -> str:
    """Skrot liczony dokladnie jak w programie "gazety" (v3.3): md5 z
    "<rozmiar>|" + 256 kB poczatku + 256 kB konca. Pusty tekst przy bledzie."""
    try:
        size = os.path.getsize(path)
        h = hashlib.md5()
        h.update(f"{size}|".encode())
        with open(path, "rb") as f:
            h.update(f.read(HASH_HEAD))
            if size > HASH_HEAD + HASH_TAIL:
                f.seek(-HASH_TAIL, os.SEEK_END)
                h.update(f.read(HASH_TAIL))
        return HASH_MARK + ":" + h.hexdigest()
    except OSError:
        return ""


def norm_path(p: str | None) -> str:
    """Sciezka do porownan: jednolite ukosniki, bez rozrozniania wielkosci liter."""
    return (p or "").strip().replace("\\", "/").lower()


def _read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _mb(text: str) -> float | None:
    try:
        return float((text or "").replace(",", ".").strip())
    except ValueError:
        return None


def load_report(path: str | Path) -> list[dict]:
    """Wiersze raportu jako slowniki. Rzuca ValueError, gdy plik nie wyglada na raport."""
    text = _read_text(path)
    first = text.split("\n", 1)[0]
    delim = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = {c.strip().lower() for c in (reader.fieldnames or [])}
    if not all(c in cols for c in REQUIRED) or not ({"sciezka", "plik", "skrot"} & cols):
        raise ValueError("brak kolumn data_koncowa oraz sciezka/plik/skrot - "
                         "to nie wyglada na raport dat")
    src = Path(path).name
    out = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        spath = row.get("sciezka", "")
        name = row.get("plik") or (re.split(r"[\\/]", spath)[-1] if spath else "")
        date = row.get("data_koncowa", "")
        full = date if naming.is_valid_date(date) else None
        number = row.get("numer", "")
        number = str(int(number)) if number.isdigit() else None
        nowa = row.get("nowa_nazwa") or name
        m = RE_SUFFIX.search(nowa)
        suffix = m.group(1).lower() if m and number else None
        m = RE_TITLE.match(nowa) or RE_TITLE.match(name)
        out.append({
            "src": src,
            "hash": row.get("skrot", ""),
            "path": norm_path(spath),
            "name": name.lower(),
            "size_mb": _mb(row.get("rozmiar_mb", "")),
            "date_iso": full,
            "date_text": date,
            "issue_number": number,
            "issue_suffix": suffix,
            "title": m.group(1) if m else None,
            "sure": row.get("status", "") == STATUS_OK and bool(full),
            "status": row.get("status", ""),
            "note": row.get("uwagi", ""),
        })
    return out


def _size_ok(size_bytes: int | None, size_mb: float | None) -> bool:
    if size_bytes is None or size_mb is None:
        return False
    return (abs(size_bytes / 1048576 - size_mb) <= 0.011
            or abs(size_bytes / 1e6 - size_mb) <= 0.011)


class Index:
    """Wszystkie zaimportowane wiersze, z szybkim wyszukiwaniem."""

    def __init__(self):
        self.by_hash: dict[str, list[dict]] = {}
        self.by_path: dict[str, list[dict]] = {}
        self.by_name: dict[str, list[dict]] = {}
        self.sources: list[str] = []

    def add(self, entries: list[dict]):
        for e in entries:
            if e["hash"]:
                self.by_hash.setdefault(e["hash"], []).append(e)
            if e["path"]:
                self.by_path.setdefault(e["path"], []).append(e)
            if e["name"]:
                self.by_name.setdefault(e["name"], []).append(e)
        if entries and entries[0]["src"] not in self.sources:
            self.sources.append(entries[0]["src"])

    def has_hashes(self) -> bool:
        return bool(self.by_hash)

    def find(self, rec: dict) -> list[dict]:
        """Pasujace wiersze ze wszystkich raportow (moze byc kilka)."""
        h = rec.get("h2")
        if h and h in self.by_hash:
            return self.by_hash[h]
        p = norm_path(rec.get("path"))
        if p in self.by_path:
            return self.by_path[p]
        cands = self.by_name.get((rec.get("old_name") or "").lower(), [])
        return [e for e in cands if _size_ok(rec.get("size"), e["size_mb"])]


def _key(e: dict) -> tuple:
    return (e["date_iso"], e["issue_number"], e["issue_suffix"])


def apply(rec: dict, matches: list[dict]) -> str:
    """Przenosi wiedze z raportu do rekordu. Zwraca: 'pewne', 'watpliwe',
    'sprzeczne', 'reczny' (rekord poprawiony recznie - nie ruszamy) albo ''."""
    if not matches:
        return ""
    st = (rec.get("status") or "").lower()
    if "recznie" in st:
        return "reczny"
    sure = [e for e in matches if e["sure"]]
    srcs = ", ".join(sorted({e["src"] for e in matches}))
    variants = {_key(e) for e in sure}
    if len(variants) > 1:
        opis = " / ".join(f"{d} nr {n or '?'}" for d, n, _ in sorted(variants, key=str))
        rec["report_flag"] = True
        rec["note"] = f"raporty sie roznia: {opis} ({srcs})"
        rec["status"] = "raport: sprzecznosc"
        return "sprzeczne"
    best = sure[0] if sure else matches[-1]
    if sure:
        rec["date_iso"] = best["date_iso"]
        rec["issue_number"] = best["issue_number"] or rec.get("issue_number")
        rec["issue_suffix"] = best["issue_suffix"]
        rec["title"] = rec.get("title") or best["title"]
        rec["confidence"] = 1.0
        rec["status"] = "z raportu"
        rec["note"] = f"raport: {srcs}"
        rec["outlier"] = False
        rec["outlier_info"] = ""
        rec["report_flag"] = False
        rec["checked"] = False  # nie wysylamy juz do AI
        return "pewne"
    # watpliwe / brak - tylko podpowiedz, dane wpisujemy gdy rekord ich nie ma
    if best["date_iso"] and not rec.get("date_iso"):
        rec["date_iso"] = best["date_iso"]
    if best["issue_number"] and not rec.get("issue_number"):
        rec["issue_number"] = best["issue_number"]
        rec["issue_suffix"] = best["issue_suffix"]
    rec["title"] = rec.get("title") or best["title"]
    hint = f"raport ({best['status'] or 'brak'}): {best['date_text'] or '?'} nr {best['issue_number'] or '?'}"
    if best["note"]:
        hint += f" - {best['note']}"
    rec["note"] = hint
    rec["report_flag"] = True
    if st in ("nowy", ""):
        rec["status"] = "raport: watpliwe"
    return "watpliwe"

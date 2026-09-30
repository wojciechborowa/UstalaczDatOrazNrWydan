"""Jednoznaczne dane z nazwy pliku - do porownania z odczytem AI.

Zrodla, od najpewniejszego:
  1. wzorzec nazwy (wbudowany albo wlasny) - pola sa nazwane, wiec nie ma zgadywania,
  2. dla nazw "luznych" tylko zapisy, ktorych nie da sie odczytac inaczej:
       pelna data liczbowa   1958-06-17, 17.06.1958, 17-06-1958
       pelna data slowna     17 juin 1958, 17 de junho de 1958, June 17, 1958
       miesiac slownie i rok juin 1958
       czterocyfrowy rok     1958 (tylko gdy w nazwie jest dokladnie jeden)
       oznaczony numer       nr 638, n° 638, no. 638, numero 638, #638
Samotna liczba (np. "12") jest pomijana - nie wiadomo, czy to dzien, numer czy strona.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from pathlib import Path

YEAR_MIN, YEAR_MAX = 1850, 2035

# Pelne nazwy miesiecy i ich odmiany oraz typowe skroty. W nazwach plikow miesiac
# slowny liczy sie tylko obok liczby (dnia albo roku), wiec krotkie skroty sa bezpieczne.
_MONTHS = {
    1: "janvier januar january janeiro gennaio enero styczen stycznia jan janv gen ene sty",
    2: "fevrier februar february fevereiro febbraio febrero luty lutego feb fev fevr febr lut",
    3: "mars marz maerz march marco marzo marzec marca mar",
    4: "avril april abril aprile kwiecien kwietnia apr avr abr kwi",
    5: "mai may maio maggio mayo maj maja mag",
    6: "juin juni june junho giugno junio czerwiec czerwca jun giu cze",
    7: "juillet juli july julho luglio julio lipiec lipca jul juil lug lip",
    8: "aout august agosto sierpien sierpnia aug ago aou sie",
    9: "septembre september setembro settembre septiembre wrzesien wrzesnia sep sept set wrz",
    10: "octobre oktober october outubro ottobre octubre pazdziernik pazdziernika oct okt out ott paz",
    11: "novembre november novembro noviembre listopad listopada nov lis",
    12: "decembre dezember december dezembro dicembre diciembre grudzien grudnia dec dez dic gru",
}
MONTH_WORDS = {w: m for m, words in _MONTHS.items() for w in words.split()}


def fold(text: str) -> str:
    """Male litery bez znakow diakrytycznych ('Marz' -> 'marz', 'wrzesnia')."""
    norm = unicodedata.normalize("NFKD", str(text).lower()).replace("ł", "l")
    return "".join(c for c in norm if not unicodedata.combining(c))


def month_of(word: str) -> int | None:
    return MONTH_WORDS.get(fold(word).strip(". "))


def valid_date(y, m, d) -> str | None:
    try:
        return _dt.date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def _year_ok(y) -> bool:
    return y is not None and YEAR_MIN <= int(y) <= YEAR_MAX


_W = r"[A-Za-zÀ-ÿĀ-žłŁ]+\.?"         # slowo (miesiac)
_SEP = r"[\s._,-]*(?:de\s+)?"          # "17 de junho de 1958", "17-juin-1958"
RE_ISO = re.compile(r"(?<!\d)(\d{4})[-._ ](\d{1,2})[-._ ](\d{1,2})(?!\d)")
RE_DMY = re.compile(r"(?<!\d)(\d{1,2})[-._ ](\d{1,2})[-._ ](\d{4})(?!\d)")
RE_D_MON_Y = re.compile(rf"(?<!\d)(\d{{1,2}}){_SEP}({_W}){_SEP}(\d{{4}})(?!\d)", re.I)
RE_MON_D_Y = re.compile(rf"(?<![A-Za-z])({_W})[\s._-]*(\d{{1,2}})(?:st|nd|rd|th)?[\s,._-]+(\d{{4}})(?!\d)", re.I)
RE_MON_Y = re.compile(rf"(?<![A-Za-z])({_W}){_SEP}(\d{{4}})(?!\d)", re.I)
RE_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
RE_ISSUE = re.compile(r"(?:(?<![A-Za-z])(?:nr|no|num|numero|número|heft|n°|nº|n\.)|#)\s*\.?\s*(\d{1,6})(?!\d)",
                      re.I)


def from_loose_name(stem: str) -> dict:
    """Jednoznaczne dane z nazwy, ktora nie pasuje do zadnego wzorca."""
    out: dict = {}
    s = stem

    def take(y, m, d=None):
        if not _year_ok(y) or not 1 <= int(m) <= 12:
            return False
        if d is not None and not valid_date(y, m, d):
            return False
        out.update(year=int(y), month=int(m))
        if d is not None:
            out["day"] = int(d)
        return True

    found = False
    for m in RE_ISO.finditer(s):
        if take(m.group(1), m.group(2), m.group(3)):
            found = True
            break
    if not found:
        for m in RE_DMY.finditer(s):
            if take(m.group(3), m.group(2), m.group(1)):
                found = True
                break
    if not found:
        for m in RE_D_MON_Y.finditer(s):
            mon = month_of(m.group(2))
            if mon and take(m.group(3), mon, m.group(1)):
                found = True
                break
    if not found:
        for m in RE_MON_D_Y.finditer(s):
            mon = month_of(m.group(1))
            if mon and take(m.group(3), mon, m.group(2)):
                found = True
                break
    if not found:
        for m in RE_MON_Y.finditer(s):
            mon = month_of(m.group(1))
            if mon and take(m.group(2), mon):
                found = True
                break

    im = RE_ISSUE.search(s)
    if im:
        out["issue"] = str(int(im.group(1)))

    if "year" not in out:
        rest = RE_ISSUE.sub(" ", s)     # "nr 1958" to numer, nie rok
        years = {int(y) for y in RE_YEAR.findall(rest) if _year_ok(int(y))}
        if len(years) == 1:
            out["year"] = years.pop()
    return out


def facts(rec: dict) -> dict:
    """Dane z nazwy do porownania: {year, month, day, issue, suffix, source}.

    Gdy nazwa pasuje do wzorca - pola wzorca. W przeciwnym razie tylko
    jednoznaczne zapisy z nazwy luznej."""
    nd = rec.get("name_data")
    if nd:
        out = {k: nd.get(k) for k in ("year", "month", "day", "issue", "suffix") if nd.get(k)}
        out["source"] = "wzorzec"
        return out
    stem = Path(rec.get("old_name") or rec.get("path") or "").stem
    out = from_loose_name(stem)
    if out:
        out["source"] = "nazwa"
    return out

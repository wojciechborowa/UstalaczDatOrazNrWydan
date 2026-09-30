"""Walidacja krzyzowa numer wydania <-> data.

Periodyk wydaje numery liniowo w czasie. Po dopasowaniu odpornej prostej
(estymator Theila-Sena) do par (data, numer) kazdy rekord mocno odstajacy
od tej prostej jest niemal na pewno bledem odczytu. To najskuteczniejsze
i darmowe sito bledow, jakie mamy - dziala lepiej niz pole "pewnosc" modelu.
"""
from __future__ import annotations

import datetime as _dt
import random
import re
import unicodedata
from statistics import median

from naming import is_valid_date

MIN_POINTS = 6          # ponizej tylu rekordow regresja nie ma sensu
MAX_PAIRS = 40000       # gorny limit par przy estymacji Theila-Sena
RESIDUAL_FLOOR = 2.0    # minimalna tolerancja w "numerach wydania"
MAD_MULTIPLIER = 4.0

# Nazwy miesiecy w jezykach, w ktorych zwykle wychodza te czasopisma.
# Wystarczaja pierwsze 3-4 litery, wiec tablica pokrywa tez skroty i odmiany.
_MONTH_WORDS = {
    1:  ["janvier", "januar", "january", "janeiro", "gennaio", "enero", "stycz", "jan", "gen", "ene"],
    2:  ["fevrier", "februar", "february", "fevereiro", "febbraio", "febrero", "luty", "lut", "feb", "fev"],
    3:  ["mars", "marz", "march", "marco", "marzo", "marzec", "mar"],
    4:  ["avril", "april", "abril", "aprile", "kwiec", "apr", "abr", "avr"],
    5:  ["mai", "may", "maio", "maggio", "mayo", "maj"],
    6:  ["juin", "juni", "june", "junho", "giugno", "junio", "czerw", "jun", "giu"],
    7:  ["juillet", "juli", "july", "julho", "luglio", "julio", "lip", "jul", "lug"],
    8:  ["aout", "august", "agosto", "sierp", "ago", "aug", "aou"],
    9:  ["septembre", "september", "setembro", "settembre", "septiembre", "wrzes", "sep", "set"],
    10: ["octobre", "oktober", "october", "outubro", "ottobre", "octubre", "pazdz", "oct", "okt", "out", "ott"],
    11: ["novembre", "november", "novembro", "noviembre", "listop", "nov"],
    12: ["decembre", "dezember", "december", "dezembro", "dicembre", "diciembre", "grudz", "dec", "dez", "dic", "gru"],
}


def _fold(text: str) -> str:
    """Bez znakow diakrytycznych i malymi literami - 'Marz', 'marco', 'aout' porownywalne."""
    norm = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in norm if not unicodedata.combining(c))


def month_from_words(text: str | None) -> int | None:
    """Numer miesiaca odczytany z nazwy w dowolnym z obslugiwanych jezykow."""
    if not text:
        return None
    folded = _fold(text)
    best: tuple[int, int] | None = None   # (dlugosc dopasowania, miesiac)
    for num, words in _MONTH_WORDS.items():
        for w in words:
            if w in folded and (best is None or len(w) > best[0]):
                best = (len(w), num)
    return best[1] if best else None


def parse_day_month(rec: dict) -> tuple[int | None, int | None]:
    """Dzien i miesiac z 'date_raw'/'month_raw', gdy sam rok jest nieczytelny.

    Najpierw probujemy nazwy miesiaca (to jest wiarygodne), potem zapisu
    czysto liczbowego, przy czym trzymamy sie kolejnosci dzien-miesiac.
    """
    raw = str(rec.get("date_raw") or "")
    month = month_from_words(rec.get("month_raw")) or month_from_words(raw)

    if month is not None:
        m = re.search(r"\b(\d{1,2})\b", raw)
        if m:
            day = int(m.group(1))
            if 1 <= day <= 31:
                return day, month
        return None, month

    m = re.search(r"\b(\d{1,2})\b\s*[./-]\s*\b(\d{1,2})\b", raw)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            return day, month
        if 1 <= month <= 31 and 1 <= day <= 12:   # zapis odwrocony
            return month, day
    return None, None


def _ordinal(date_iso: str) -> int | None:
    try:
        y, m, d = (int(x) for x in date_iso.split("-"))
        return _dt.date(y, m, d).toordinal()
    except Exception:
        return None


def _numeric_issue(rec: dict) -> int | None:
    raw = rec.get("issue_number")
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return int(digits) if digits else None


def _norm_title(rec: dict) -> str:
    return re.sub(r"\s+", " ", str(rec.get("title") or "").strip().lower())


def _theil_sen(points: list[tuple[int, int]]) -> tuple[float, float]:
    """Zwraca (nachylenie, wyraz wolny) odporne na wartosci odstajace."""
    n = len(points)
    slopes: list[float] = []
    total_pairs = n * (n - 1) // 2
    if total_pairs <= MAX_PAIRS:
        for i in range(n):
            xi, yi = points[i]
            for j in range(i + 1, n):
                xj, yj = points[j]
                if xj != xi:
                    slopes.append((yj - yi) / (xj - xi))
    else:
        rnd = random.Random(12345)
        for _ in range(MAX_PAIRS):
            i, j = rnd.randrange(n), rnd.randrange(n)
            if i == j:
                continue
            xi, yi = points[i]
            xj, yj = points[j]
            if xj != xi:
                slopes.append((yj - yi) / (xj - xi))
    if not slopes:
        return 0.0, 0.0
    slope = median(slopes)
    intercept = median(y - slope * x for x, y in points)
    return slope, intercept


def cross_check(records: list[dict]) -> dict:
    """Ustawia rec['outlier'] i rec['outlier_info']. Zwraca podsumowanie per tytul."""
    for rec in records:
        rec["outlier"] = False
        rec["outlier_info"] = ""

    groups: dict[str, list[dict]] = {}
    for rec in records:
        if not is_valid_date(rec.get("date_iso")):
            continue
        if _numeric_issue(rec) is None:
            continue
        # osobno dla kazdego roku: numeracja potrafi zaczac sie od nowa, a wtedy jedna
        # prosta dla calej kolekcji oznaczalaby poprawne rekordy jako podejrzane
        year = str(rec["date_iso"])[:4]
        groups.setdefault(f"{_norm_title(rec)} {year}".strip(), []).append(rec)

    summary: dict[str, dict] = {}

    for title, recs in groups.items():
        if len(recs) < MIN_POINTS:
            summary[title or "(bez tytulu)"] = {
                "count": len(recs), "checked": False,
                "reason": f"za malo rekordow ({len(recs)}), minimum {MIN_POINTS}",
                "outliers": 0,
            }
            continue

        pts: list[tuple[int, int]] = []
        keep: list[dict] = []
        for rec in recs:
            x = _ordinal(rec["date_iso"])
            y = _numeric_issue(rec)
            if x is None or y is None:
                continue
            pts.append((x, y))
            keep.append(rec)

        if len(pts) < MIN_POINTS:
            continue

        slope, intercept = _theil_sen(pts)
        residuals = [y - (slope * x + intercept) for x, y in pts]
        med = median(residuals)
        mad = median(abs(r - med) for r in residuals)
        tol = max(RESIDUAL_FLOOR, MAD_MULTIPLIER * mad if mad > 0 else RESIDUAL_FLOOR)

        n_out = 0
        for rec, res in zip(keep, residuals):
            if abs(res - med) > tol:
                rec["outlier"] = True
                expected = round(slope * _ordinal(rec["date_iso"]) + intercept + med)
                rec["outlier_info"] = (
                    f"numer odstaje od ciagu (oczekiwano ok. {expected}, "
                    f"odczytano {_numeric_issue(rec)})")
                n_out += 1

        days_per_issue = (1.0 / slope) if slope else 0.0
        summary[title or "(bez tytulu)"] = {
            "count": len(pts), "checked": True, "outliers": n_out,
            "slope": slope, "days_per_issue": days_per_issue,
            "reason": "",
        }

    return summary


def fill_missing_years(records: list[dict]) -> int:
    """Uzupelnia brakujacy rok tam, gdzie model odczytal dzien i miesiac, ale nie rok.

    Rok bierzemy z sasiadow w ciagu numerow tego samego tytulu. Zwraca liczbe uzupelnien.
    """
    filled = 0
    groups: dict[str, list[dict]] = {}
    for rec in records:
        if _numeric_issue(rec) is not None:
            groups.setdefault(_norm_title(rec), []).append(rec)

    for recs in groups.values():
        anchors: list[tuple[int, int]] = []
        for r in recs:
            n = _numeric_issue(r)
            if n is None or not is_valid_date(r.get("date_iso")):
                continue
            o = _ordinal(r["date_iso"])
            if o is not None:
                anchors.append((n, o))
        if len(anchors) < MIN_POINTS:
            continue

        # x = numer wydania, y = data -> z numeru szacujemy date, a z niej rok
        slope, intercept = _theil_sen(anchors)
        if slope <= 0:
            continue

        for rec in recs:
            if is_valid_date(rec.get("date_iso")):
                continue
            num = _numeric_issue(rec)
            if num is None:
                continue
            day, month = parse_day_month(rec)
            if day is None or month is None:
                continue

            est_ord = slope * num + intercept
            if not (1 <= est_ord <= _dt.date.max.toordinal()):
                continue

            # rok kandydujacy plus sasiednie - data moze wypasc tuz za granica roku
            base_year = _dt.date.fromordinal(int(round(est_ord))).year
            best: tuple[float, _dt.date] | None = None
            for year in (base_year - 1, base_year, base_year + 1):
                try:
                    cand = _dt.date(year, month, day)
                except ValueError:
                    continue
                dist = abs(cand.toordinal() - est_ord)
                if best is None or dist < best[0]:
                    best = (dist, cand)

            if best and best[0] <= 120:      # do ~4 miesiecy od oszacowania
                rec["date_iso"] = best[1].isoformat()
                note = (rec.get("note") or "").replace("nie odczytano: data", "").strip(" ,")
                rec["note"] = (note + " " if note else "") + "[rok uzupelniony z ciagu numerow]"
                filled += 1

    return filled

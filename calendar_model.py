"""Kalendarz wydan: numer wydania -> data (i odwrotnie).

Kotwice to pewne pary (numer, data): z raportow, z rekordow poprawionych recznie
i z pewnych odczytow AI. Date dla numeru N wyliczamy z kilku najblizszych kotwic
ponizej i powyzej N (interpolacja liniowa), biorac mediane z wielu par sasiadow -
jedna bledna kotwica nie psuje wyniku. Pary, miedzy ktorymi data sie cofa
(reset numeracji) albo krok jest nierealny, sa pomijane.

Przy sprawdzaniu rekordu jego wlasna kotwica jest wylaczana - zgodnosc z kalendarzem
znaczy wtedy "sasiednie, niezalezne wydania potwierdzaja te date".
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from bisect import bisect_left
from dataclasses import dataclass
from statistics import median

NEIGHBOURS = 3          # ile kotwic z kazdej strony bierzemy do interpolacji
MAX_GAP = 60            # max roznica numerow miedzy kotwicami uzytymi do interpolacji
MAX_EXTRAPOLATE = 8     # o ile numerow wolno wyjsc poza ostatnia kotwice
MIN_STEP, MAX_STEP = 0.4, 45.0   # realny odstep miedzy numerami (dni)
PERIOD_DAYS = 550       # przy znanym okresie (rok z nazwy) kotwice tylko z +-1,5 roku
STRONG_TOL = 3          # zgodnosc z kalendarzem liczy sie jako potwierdzenie do tej tolerancji


def ordinal(date_iso: str | None) -> int | None:
    try:
        y, m, d = (int(x) for x in str(date_iso).split("-"))
        return _dt.date(y, m, d).toordinal()
    except Exception:
        return None


def iso(o: float) -> str:
    return _dt.date.fromordinal(int(round(o))).isoformat()


def issue_int(value) -> int | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return int(digits) if digits else None


def norm_suffix(value) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def norm_title(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


@dataclass
class Prediction:
    date_iso: str
    tol: int            # tolerancja w dniach
    basis: str          # skad wiadomo - do pokazania uzytkownikowi
    exact: bool = False # ten sam numer jest w kotwicach

    def agrees(self, date_iso: str | None) -> bool:
        o = ordinal(date_iso)
        return o is not None and abs(o - ordinal(self.date_iso)) <= self.tol


class _Series:
    """Kotwice jednego tytulu."""

    def __init__(self):
        self.exact: dict[tuple[int, str], list[tuple[int, object]]] = {}
        self.base: dict[int, list[tuple[int, object]]] = {}
        self._keys: list[int] | None = None

    def add(self, issue: int, suffix: str, o: int, src):
        self.exact.setdefault((issue, suffix), []).append((o, src))
        if not suffix:
            self.base.setdefault(issue, []).append((o, src))
            self._keys = None

    def keys(self) -> list[int]:
        if self._keys is None:
            self._keys = sorted(self.base)
        return self._keys

    def base_date(self, issue: int, exclude, near: int | None = None) -> int | None:
        vals = [o for o, s in self.base.get(issue, [])
                if (exclude is None or s is not exclude) and (near is None or abs(o - near) <= PERIOD_DAYS)]
        return int(median(vals)) if vals else None

    def count(self) -> int:
        return sum(len(v) for v in self.exact.values())


class Calendar:
    def __init__(self):
        self.series: dict[str, _Series] = {}

    # ------------------------------------------------------------ budowa
    def add(self, title, issue, suffix, date_iso, src=None) -> bool:
        n, o = issue_int(issue), ordinal(date_iso)
        if n is None or o is None:
            return False
        self.series.setdefault(norm_title(title), _Series()).add(n, norm_suffix(suffix), o, src)
        return True

    def anchors(self) -> int:
        return sum(s.count() for s in self.series.values())

    def _series_for(self, title) -> _Series | None:
        s = self.series.get(norm_title(title))
        if s is not None:
            return s
        # rekord bez tytulu (albo tytul inaczej zapisany), a kalendarz ma jedna serie
        named = [v for v in self.series.values() if v.count()]
        return named[0] if len(named) == 1 else None

    # ------------------------------------------------------------ numer -> data
    def predict(self, title, issue, suffix=None, exclude=None, near: int | None = None) -> Prediction | None:
        """near: ordinal daty, wokol ktorej szukamy kotwic (np. polowa roku z nazwy pliku).
        Potrzebne, gdy numeracja wydan zaczynala sie od nowa i ten sam numer wystepuje
        w roznych latach."""
        s = self._series_for(title)
        n = issue_int(issue)
        if s is None or n is None:
            return None
        suf = norm_suffix(suffix)

        same = [o for o, src in s.exact.get((n, suf), [])
                if (exclude is None or src is not exclude) and (near is None or abs(o - near) <= PERIOD_DAYS)]
        if same:
            spread = max(same) - min(same)
            return Prediction(iso(median(same)), max(0, spread),
                              f"ten sam numer w {len(same)} pewnych zrodlach", exact=True)
        if suf:
            # "bis"/"special" bez wlasnej kotwicy - zwykle blisko numeru podstawowego
            base = s.base_date(n, exclude, near)
            if base is None:
                p = self._interpolate(s, n, exclude, near)
                if p is None:
                    return None
                base = ordinal(p.date_iso)
            return Prediction(iso(base), 7, f"blisko numeru podstawowego {n}")
        return self._interpolate(s, n, exclude, near)

    def _near(self, s: _Series, n: int, exclude, side: int, near=None) -> list[tuple[int, int]]:
        keys = s.keys()
        pos = bisect_left(keys, n)
        rng = range(pos - 1, -1, -1) if side < 0 else range(pos, len(keys))
        out = []
        for i in rng:
            k = keys[i]
            if k == n:
                continue
            o = s.base_date(k, exclude, near)
            if o is None:
                continue
            out.append((k, o))
            if len(out) >= NEIGHBOURS:
                break
        return out

    def _interpolate(self, s: _Series, n: int, exclude, near=None) -> Prediction | None:
        lo = self._near(s, n, exclude, -1, near)
        hi = self._near(s, n, exclude, +1, near)
        preds, steps = [], []
        for ln, lo_o in lo:
            for hn, hi_o in hi:
                if hn - ln > MAX_GAP or hi_o <= lo_o:
                    continue
                step = (hi_o - lo_o) / (hn - ln)
                if not MIN_STEP <= step <= MAX_STEP:
                    continue
                preds.append(lo_o + (n - ln) * step)
                steps.append(step)
        if preds:
            p = median(preds)
            spread = max(preds) - min(preds)
            step = steps[0]
            gap = hi[0][0] - lo[0][0]
            tol = max(1, math.ceil(spread))
            if abs(step - round(step)) > 0.15:   # nieregularny rytm (np. 2x w tygodniu)
                tol += 1
            if gap > 10:                          # dziura w kotwicach - mniej pewnie
                tol += 2
            return Prediction(iso(p), tol,
                              f"miedzy numerami {lo[0][0]} i {hi[0][0]} (rytm {step:.1f} dnia)")

        # tylko z jednej strony - krotkie przedluzenie ciagu
        side = lo or hi
        if len(side) >= 2 and abs(side[0][0] - n) <= MAX_EXTRAPOLATE:
            (n1, o1), (n2, o2) = side[0], side[1]
            if n1 == n2 or (o1 - o2) * (n1 - n2) <= 0:
                return None
            step = (o1 - o2) / (n1 - n2)
            if not MIN_STEP <= step <= MAX_STEP:
                return None
            dist = abs(n - n1)
            return Prediction(iso(o1 + (n - n1) * step), 1 + dist // 2,
                              f"przedluzenie ciagu od numeru {n1} (rytm {step:.1f} dnia)")
        return None

    # ------------------------------------------------------------ data -> numer
    def predict_issue(self, title, date_iso, exclude=None, near=None) -> tuple[int, str] | None:
        """Numer wydania dla daty - tylko gdy da sie go wskazac jednoznacznie."""
        s = self._series_for(title)
        o = ordinal(date_iso)
        if s is None or o is None:
            return None
        pts = sorted((d, k) for k in s.keys()
                     for d in [s.base_date(k, exclude, near if near is not None else o)]
                     if d is not None)
        if len(pts) < 2:
            return None
        dates = [d for d, _ in pts]
        pos = bisect_left(dates, o)
        if pos < len(pts) and pts[pos][0] == o:
            return pts[pos][1], "pewne wydanie z ta sama data"
        if 0 < pos < len(pts):
            (d1, n1), (d2, n2) = pts[pos - 1], pts[pos]
            if n2 <= n1 or n2 - n1 > MAX_GAP:
                return None
            est = n1 + (o - d1) * (n2 - n1) / (d2 - d1)
            if abs(est - round(est)) > 0.25:   # data wypada miedzy wydaniami
                return None
            return int(round(est)), f"miedzy numerami {n1} i {n2}"
        return None


# ---------------------------------------------------------------- z rekordow
def near_of(r: dict) -> int | None:
    """Okres rekordu: polowa roku z nazwy pliku, a bez niego - jego wlasna data."""
    year = (r.get("name_data") or {}).get("year")
    if year:
        try:
            return _dt.date(int(year), 7, 1).toordinal()
        except ValueError:
            pass
    return ordinal(r.get("date_iso"))


def build(records: list[dict], report_entries: list[dict], min_conf: float) -> Calendar:
    """Kotwice: pewne wiersze raportow, rekordy reczne/z raportu/potwierdzone
    i odczyty AI o wysokiej pewnosci bez wlasnych watpliwosci."""
    cal = Calendar()
    for e in report_entries:
        if e.get("sure"):
            cal.add(e.get("title"), e.get("issue_number"), e.get("issue_suffix"),
                    e.get("date_iso"), src=None)
    for r in records:
        st = (r.get("status") or "").lower()
        strong = "recznie" in st or st in ("z raportu", "potwierdzone")
        ai_ok = (st.startswith("odczytano") and (r.get("confidence") or 0) >= min_conf
                 and not r.get("outlier") and not r.get("report_flag")
                 and not r.get("vote_conflict")
                 and "niejednoznaczna" not in (r.get("note") or ""))
        if strong or ai_ok:
            cal.add(r.get("title"), r.get("issue_number"), r.get("issue_suffix"),
                    r.get("date_iso"), src=r)
    return cal


def check_records(cal: Calendar, records: list[dict]) -> dict:
    """Ustawia rec['cal_state'] ('ok' / 'conflict' / None) i rec['cal_info'].

    Zwraca liczniki. Rekordow recznych nie ocenia - to one sa wzorcem."""
    counts = {"ok": 0, "conflict": 0, "none": 0}
    for r in records:
        r["cal_state"], r["cal_info"] = None, ""
        st = (r.get("status") or "").lower()
        if "recznie" in st or not r.get("date_iso") or issue_int(r.get("issue_number")) is None:
            continue
        if r.get("cal_filled"):
            continue   # wartosc wzieta z kalendarza nie moze potwierdzac sama siebie
        p = cal.predict(r.get("title"), r.get("issue_number"), r.get("issue_suffix"), exclude=r,
                        near=near_of(r))
        if p is None:
            counts["none"] += 1
            continue
        if p.agrees(r["date_iso"]):
            if p.tol <= STRONG_TOL:
                r["cal_state"] = "ok"
                r["cal_info"] = f"zgodne z kalendarzem ({p.basis})"
                counts["ok"] += 1
            else:
                r["cal_info"] = f"zgodne z kalendarzem tylko z grubsza (+-{p.tol} dni)"
                counts["none"] += 1
        else:
            r["cal_state"] = "conflict"
            tol = f" +-{p.tol} dni" if p.tol else ""
            r["cal_info"] = f"kalendarz: oczekiwano {p.date_iso}{tol} ({p.basis})"
            counts["conflict"] += 1
    return counts

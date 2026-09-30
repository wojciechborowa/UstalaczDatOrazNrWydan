"""Dopracowanie niepewnych rekordow: kalendarz wydan, drugi odczyt AI i glosowanie.

Rekord staje sie pewny ("potwierdzone") tylko wtedy, gdy zgadzaja sie co najmniej
dwa niezalezne zrodla: dwa odczyty AI albo odczyt AI i kalendarz wydan.
"""
from __future__ import annotations

from calendar_model import STRONG_TOL, Calendar, issue_int, near_of, norm_suffix


def _key(date_iso, issue, suffix) -> tuple:
    return (date_iso or None, issue_int(issue), norm_suffix(suffix))


def fill_from_calendar(cal: Calendar, recs: list[dict]) -> int:
    """Uzupelnia brakujaca date (gdy jest numer) albo numer (gdy jest data).

    To tylko kandydat - rekord zostaje do sprawdzenia (cal_filled), dopoki drugi
    odczyt go nie potwierdzi. Zwraca liczbe uzupelnien."""
    n = 0
    for r in recs:
        st = (r.get("status") or "").lower()
        if "recznie" in st:
            continue
        has_issue = issue_int(r.get("issue_number")) is not None
        if has_issue and not r.get("date_iso"):
            p = cal.predict(r.get("title"), r.get("issue_number"), r.get("issue_suffix"), exclude=r,
                            near=near_of(r))
            if p and p.tol <= 1:
                r["date_iso"] = p.date_iso
                r["cal_filled"] = True
                r["note"] = f"data z kalendarza wydan ({p.basis}) - do potwierdzenia"
                r["status"] = "data z kalendarza"
                n += 1
        elif r.get("date_iso") and not has_issue:
            got = cal.predict_issue(r.get("title"), r.get("date_iso"), exclude=r, near=near_of(r))
            if got:
                r["issue_number"] = str(got[0])
                r["cal_filled"] = True
                r["note"] = f"numer z kalendarza wydan ({got[1]}) - do potwierdzenia"
                r["status"] = "numer z kalendarza"
                n += 1
    return n


def snapshot(r: dict) -> None:
    """Zapamietuje pierwszy odczyt przed drugim."""
    r["first"] = {k: r.get(k) for k in ("date_iso", "issue_number", "issue_suffix",
                                        "confidence", "model", "title", "cal_filled")}


def _confirm(r: dict, reason: str) -> None:
    r["status"] = "potwierdzone"
    r["confidence"] = max(r.get("confidence") or 0.0, 0.95)
    r["note"] = "potwierdzone: " + reason
    r["vote_conflict"] = False
    r["cal_filled"] = False
    r["report_flag"] = False
    r["outlier"] = False


def vote(cal: Calendar, r: dict) -> str:
    """Porownuje pierwszy odczyt (r['first']), drugi (biezace pola) i kalendarz.

    Zwraca 'potwierdzone' albo 'sprzeczne'."""
    first = r.get("first") or {}
    a = _key(first.get("date_iso"), first.get("issue_number"), first.get("issue_suffix"))
    b = _key(r.get("date_iso"), r.get("issue_number"), r.get("issue_suffix"))
    title = r.get("title") or first.get("title")

    def pred(k):
        return cal.predict(title, k[1], k[2], exclude=r, near=near_of(r)) if k[1] is not None else None

    pa, pb = pred(a), pred(b)
    # para (data, numer) jest "spojna", gdy kalendarz potwierdza ja z mala tolerancja
    ok_a = bool(a[0]) and pa is not None and pa.tol <= STRONG_TOL and pa.agrees(a[0]) \
        and not first.get("cal_filled")
    ok_b = bool(b[0]) and pb is not None and pb.tol <= STRONG_TOL and pb.agrees(b[0])

    if b[0] and b[1] is not None and a == b:
        _confirm(r, "drugi odczyt AI zgodny z wartoscia z kalendarza wydan"
                 if first.get("cal_filled") else "dwa niezalezne odczyty AI zgodne")
        return "potwierdzone"
    if ok_a and ok_b and a != b:
        pass   # dwa rozne, ale kazdy spojny wynik - nie da sie rozstrzygnac bez czlowieka
    elif ok_b and (a[1] is None or a[1] == b[1] or not ok_a):
        _confirm(r, f"drugi odczyt zgodny z kalendarzem wydan ({pb.basis})")
        return "potwierdzone"
    elif ok_a and (b[1] is None or b[1] == a[1]):
        for k in ("date_iso", "issue_number", "issue_suffix"):
            r[k] = first.get(k)
        _confirm(r, f"pierwszy odczyt zgodny z kalendarzem wydan ({pa.basis})")
        return "potwierdzone"
    elif b[1] is not None and a[1:] == b[1:] and not b[0] and pb is not None and pb.tol <= 1:
        r["date_iso"] = pb.date_iso
        _confirm(r, f"numer z dwoch odczytow, data z kalendarza ({pb.basis})")
        return "potwierdzone"

    def show(k):
        d, n, s = k
        return f"{d or '?'} nr {n if n is not None else '?'}{(' ' + s) if s else ''}"
    parts = [f"odczyt 1: {show(a)}", f"odczyt 2: {show(b)}"]
    if pb is not None:
        parts.append(f"kalendarz dla nr {b[1]}: {pb.date_iso}")
    r["vote_conflict"] = True
    r["note"] = "zrodla sie roznia - " + " | ".join(parts)
    return "sprzeczne"

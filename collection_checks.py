"""Kontrole dla kolekcji stron: jedno wydanie = jedna data, chronologia lp.

1. Wszystkie strony tego samego numeru wydania maja te sama date. Gdy co najmniej dwie
   strony niezaleznie odczytano z ta sama data (i zadna nie przeczy), to potwierdzenie.
   Strony bez odczytanej daty dostaja wtedy date z pozostalych stron.
2. Pliki ustawione wg (tytul, rok z nazwy, lp) maja daty rosnace albo rowne. Rekord,
   ktory lamie kolejnosc wobec wiekszosci sasiadow, trafia do sprawdzenia. Pojedyncza
   pomylka nie oznacza sasiadow - lamie kolejnosc tylko wobec jednego z nich.
"""
from __future__ import annotations

import re
from collections import Counter

WINDOW = 3          # ilu sasiadow z kazdej strony bierzemy pod uwage


def _title(r: dict) -> str:
    nd = r.get("name_data") or {}
    return re.sub(r"\s+", " ", str(nd.get("title") or r.get("title") or "").strip().lower())


def _manual(r: dict) -> bool:
    return "recznie" in (r.get("status") or "").lower()


def _usable(r: dict) -> bool:
    """Data, na ktorej mozna sie oprzec: odczytana albo reczna, bez wlasnych watpliwosci."""
    if not r.get("date_iso"):
        return False
    if _manual(r):
        return True
    return not (r.get("vote_conflict") or r.get("year_mismatch") or r.get("cal_filled")
                or r.get("group_filled") or r.get("name_unconfirmed")
                or "niejednoznaczna" in (r.get("note") or ""))


CHRONO_MODES = {"off": "wylaczona", "warn": "tylko ostrzezenie", "on": "wplywa na pewnosc"}


def check(records: list[dict], chrono: str = "warn") -> dict:
    """chrono: 'off' - nie sprawdzamy kolejnosci lp; 'warn' - tylko uwaga, kolor bez
    zmian; 'on' - naruszenie chronologii = rekord do sprawdzenia."""
    counts = {"group_ok": 0, "group_conflict": 0, "group_filled": 0, "chrono": 0}
    for r in records:
        r["group_ok"] = False
        r["group_conflict"] = False
        r["chrono_flag"] = False
        r["chrono_info"] = ""

    # --- 1. strony jednego wydania
    groups: dict[tuple, list[dict]] = {}
    for r in records:
        nd = r.get("name_data") or {}
        issue = nd.get("issue") or r.get("issue_number")
        if not issue or not nd:
            continue    # tylko pliki, ktorych numer znamy z nazwy
        key = (_title(r), str(issue).lstrip("0"),
               str(nd.get("suffix") or r.get("issue_suffix") or "").strip().lower())
        groups.setdefault(key, []).append(r)

    for recs in groups.values():
        if len(recs) < 2:
            continue
        manual = {r["date_iso"] for r in recs if _manual(r) and r.get("date_iso")}
        votes = Counter(r["date_iso"] for r in recs if _usable(r) and not r.get("group_filled"))
        if len(manual) == 1:
            best, n_best, others = next(iter(manual)), 99, 0
        elif votes:
            (best, n_best), = votes.most_common(1)
            others = sum(votes.values()) - n_best
        else:
            continue
        for r in recs:
            if _manual(r):
                continue
            d = r.get("date_iso")
            if d and d != best and not r.get("group_filled"):
                r["group_conflict"] = True
                r["chrono_info"] = f"inne strony tego wydania maja date {best} ({n_best} str.)"
                counts["group_conflict"] += 1
            elif n_best >= 2 and others == 0:
                if not d or r.get("group_filled"):
                    if not d:
                        r["date_iso"] = best
                        r["group_filled"] = True
                        r["status"] = "data z innych stron wydania"
                        counts["group_filled"] += 1
                    r["chrono_info"] = f"data z pozostalych stron tego wydania ({n_best} str.)"
                    r["group_ok"] = True
                elif d == best and _usable(r):
                    r["group_ok"] = True
                    r["chrono_info"] = f"zgodne z pozostalymi stronami tego wydania ({n_best} str.)"
                    counts["group_ok"] += 1

    # --- 2. chronologia wg lp
    if chrono == "off":
        return counts
    series: dict[str, list[tuple]] = {}
    for r in records:
        nd = r.get("name_data") or {}
        if not nd.get("lp") or not str(nd["lp"]).isdigit():
            continue
        series.setdefault(_title(r), []).append((nd.get("year") or 0, int(nd["lp"]), r))
    for items in series.values():
        items.sort(key=lambda t: (t[0], t[1]))
        dated = [t[2] for t in items if _usable(t[2]) or t[2].get("group_filled")]
        for i, r in enumerate(dated):
            if _manual(r):
                continue
            before = dated[max(0, i - WINDOW):i]
            after = dated[i + 1:i + 1 + WINDOW]
            if len(before) + len(after) < 2:
                continue
            d = r["date_iso"]
            late = [b for b in before if b["date_iso"] > d]
            early = [a for a in after if a["date_iso"] < d]
            bad = len(late) + len(early)
            if bad >= 2 and bad * 2 >= len(before) + len(after):
                r["chrono_flag"] = chrono == "on"   # 'warn': sama uwaga, bez wplywu na kolor
                ref = (late or early)[0]
                lp = (ref.get("name_data") or {}).get("lp")
                if late:
                    info = f"chronologia: wczesniejszy plik lp {lp} ma pozniejsza date {ref['date_iso']}"
                else:
                    info = f"chronologia: pozniejszy plik lp {lp} ma wczesniejsza date {ref['date_iso']}"
                prev = r.get("chrono_info") or ""
                r["chrono_info"] = f"{prev}; {info}" if prev else info
                counts["chrono"] += 1
    return counts

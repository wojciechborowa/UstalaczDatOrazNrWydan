"""Ocena rekordu po odczycie AI: zielony albo do weryfikacji - nic pomiedzy.

Zasady (obie kolekcje):
  * liczy sie tylko odpowiedz AI oraz dane wejsciowe: nazwa pliku i zaimportowane raporty,
  * nic nie jest uzupelniane z kalendarza, interpolacji ani sasiednich plikow,
  * odczyt kompletny i zgodny z nazwa pliku (oraz z raportem) = zielony,
  * odczyt niekompletny, niejednoznaczny albo niezgodny z nazwa / raportem = do weryfikacji,
    z krotkim powodem po ludzku,
  * przy niezgodnosci wygrywa nazwa pliku - to ona trafia do pol rekordu,
    a to, co odczytalo AI, widac w kolumnie Uwagi i w oknie weryfikacji.

Kolekcja wydan: AI ustala tytul, date i numer wydania (z wybranej strony PDF-a).
Kolekcja stron: tytul, rok, numer i strona sa w nazwie pliku - AI ustala dzien i miesiac.
"""
from __future__ import annotations

import json
import re

from name_facts import valid_date

MODE_ISSUES = "wydania"
MODE_PAGES = "strony"
MODES = {MODE_ISSUES: "Kolekcja wydan", MODE_PAGES: "Kolekcja stron"}

MONTHS_PL = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca",
             "sierpnia", "wrzesnia", "pazdziernika", "listopada", "grudnia"]

STATUS_OK = "odczytano"
STATUS_CHECK = "do weryfikacji"


def _digits(v) -> str | None:
    d = re.sub(r"\D", "", str(v or ""))
    return str(int(d)) if d else None


def _date_parts(iso) -> tuple[int, int, int] | None:
    """(rok, miesiac, dzien) z tekstu RRRR-MM-DD - bez sprawdzania, czy taki dzien istnieje."""
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", str(iso or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def ai_values(ai: dict) -> dict:
    """Znormalizowane wartosci z odpowiedzi modelu."""
    alts = []
    for a in ai.get("date_alternatives") or []:
        p = _date_parts(a)
        iso = valid_date(*p) if p else None
        if iso and iso not in alts:
            alts.append(iso)
    p = _date_parts(ai.get("date_iso"))
    date = valid_date(*p) if p else None
    return {
        "date": date,
        "bad_date": ai.get("date_iso") if p and not date else None,
        "alts": alts,
        "issue": _digits(ai.get("issue_number")),
        "suffix": (str(ai.get("issue_suffix")).strip() or None) if ai.get("issue_suffix") else None,
        "title": ai.get("publication_title"),
        "page": _digits(ai.get("page_number")),
    }


def evaluate(rec: dict, mode: str) -> None:
    """Ustawia pola rekordu na podstawie rec['ai'], nazwy pliku i raportow.

    Rekordy nieodczytane, z bledem odczytu, poprawione recznie i po zmianie nazwy
    zostaja bez zmian."""
    ai = rec.get("ai")
    st = (rec.get("status") or "").lower()
    if not isinstance(ai, dict) or any(w in st for w in ("recznie", "zmieniono", "cofnieto", "blad")):
        return
    nf = rec.get("name_facts") or {}
    nd = rec.get("name_data") or {}
    v = ai_values(ai)
    issues: list[str] = []
    info: list[str] = []

    rec["title"] = nd.get("title") or v["title"]
    rec["page_number"] = nd.get("page") or v["page"]
    for k in ("language", "date_raw", "month_raw", "year_printed", "is_cover"):
        rec[k] = ai.get(k)
    try:
        rec["confidence"] = round(float(ai.get("confidence")), 2)
    except (TypeError, ValueError):
        rec["confidence"] = None
    rec["ai_date"] = v["date"]
    rec["ai_issue"] = (v["issue"] + (f" {v['suffix']}" if v["suffix"] else "")) if v["issue"] else None

    # ------------------------------------------------------------------ data
    date = v["date"]
    if not date and len(v["alts"]) > 1:
        issues.append("AI nie rozstrzygnelo daty - mozliwe: " + " albo ".join(v["alts"]))
    elif v["bad_date"]:
        issues.append(f"AI podalo date, ktora nie istnieje ({v['bad_date']})")
    elif not date:
        issues.append("AI nie odczytalo daty" if mode == MODE_ISSUES
                      else "AI nie odczytalo dnia i miesiaca")
    elif len(v["alts"]) > 1:
        issues.append("data niejednoznaczna: " + " albo ".join(v["alts"]))

    if date:
        ay, am, ad = (int(x) for x in date.split("-"))
        y, m, d = ay, am, ad
        if nf.get("year") and int(nf["year"]) != ay:
            issues.append(f"AI odczytalo rok {ay}, a w nazwie pliku jest {nf['year']}")
            y = int(nf["year"])
        if nf.get("month") and int(nf["month"]) != am:
            issues.append(f"AI odczytalo miesiac {am:02d}, a w nazwie pliku jest {int(nf['month']):02d}")
            m = int(nf["month"])
        if nf.get("day") and int(nf["day"]) != ad:
            issues.append(f"AI odczytalo dzien {ad:02d}, a w nazwie pliku jest {int(nf['day']):02d}")
            d = int(nf["day"])
        date = valid_date(y, m, d)
        if not date:
            issues.append(f"data {d:02d}.{m:02d}.{y} nie istnieje - AI odczytalo {v['date']}")
    elif nf.get("year") and nf.get("month") and nf.get("day"):
        # AI nie dalo daty, ale nazwa ja ma - zostaje w polach jako propozycja do sprawdzenia
        date = valid_date(nf["year"], nf["month"], nf["day"])
        if date:
            info.append(f"data z nazwy pliku ({date}) niepotwierdzona przez AI")
    rec["date_iso"] = date

    # ----------------------------------------------------------------- numer
    name_issue = _digits(nf.get("issue"))
    if name_issue:
        rec["issue_number"] = name_issue
        rec["issue_suffix"] = nf.get("suffix") or (v["suffix"] if v["issue"] == name_issue else None)
        if mode == MODE_ISSUES and v["issue"] and v["issue"] != name_issue:
            issues.append(f"AI odczytalo nr {v['issue']}, a w nazwie pliku jest nr {name_issue}")
    else:
        rec["issue_number"] = v["issue"]
        rec["issue_suffix"] = v["suffix"] if v["issue"] else None
        if mode == MODE_ISSUES and not v["issue"]:
            issues.append("AI nie odczytalo numeru wydania")

    # ---------------------------------------------------------------- raport
    for e in rec.get("report_data") or []:
        src = e.get("src") or "raport"
        if e.get("date_iso") and rec["date_iso"] and e["date_iso"] != rec["date_iso"]:
            issues.append(f"raport {src} podaje date {e['date_iso']}")
        rn = _digits(e.get("issue_number"))
        if mode == MODE_ISSUES and rn and rec.get("issue_number") and rn != _digits(rec["issue_number"]):
            issues.append(f"raport {src} podaje nr {rn}")

    if not issues and nf:
        what = [k for k, label in (("year", "rok"), ("month", "miesiac"), ("day", "dzien"),
                                   ("issue", "numer")) if nf.get(k)]
        if mode == MODE_PAGES:
            what = [w for w in what if w != "issue"]
        if what:
            info.append("zgodne z nazwa pliku")
    if not issues and rec.get("report_data"):
        info.append("zgodne z raportem")

    rec["issues"] = issues
    rec["status"] = STATUS_CHECK if issues else STATUS_OK
    rec["note"] = "; ".join(issues + info)


def ai_from_legacy(rec: dict) -> dict | None:
    """Odpowiedz AI dla rekordu ze starszej sesji (bez pola 'ai').

    Najpierw z surowej odpowiedzi zapisanej przy rekordzie; gdy to cala paczka
    albo nic - z pol rekordu, ale bez wartosci wstawionych przez dawne kontrole
    (kalendarz wydan, strony tego samego wydania)."""
    raw = rec.get("raw")
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, dict) and ("date_iso" in data or "issue_number" in data):
            return data
    st = (rec.get("status") or "").lower()
    if st in ("", "nowy") or "blad" in st or "raport" in st or not rec.get("model"):
        return None
    filled = rec.get("cal_filled") or rec.get("group_filled")
    return {
        "publication_title": rec.get("title"),
        "date_iso": None if filled else rec.get("date_iso"),
        "issue_number": None if filled and "numer" in st else rec.get("issue_number"),
        "issue_suffix": rec.get("issue_suffix"),
        "page_number": rec.get("page_number"),
        "language": rec.get("language"),
        "date_raw": rec.get("date_raw"),
        "month_raw": rec.get("month_raw"),
        "year_printed": rec.get("year_printed"),
        "is_cover": rec.get("is_cover"),
        "confidence": rec.get("confidence"),
        "date_alternatives": [],
    }


def guess_mode(records: list[dict]) -> str:
    """Propozycja trybu dla starej sesji: same PDF-y = wydania, obrazy = strony."""
    pdf = sum(1 for r in records if r.get("kind") == "pdf")
    img = sum(1 for r in records if r.get("kind") == "image")
    return MODE_PAGES if img > pdf else MODE_ISSUES

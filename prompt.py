"""Uniwersalny prompt (bez nazwy konkretnego tytulu) i odporny parser odpowiedzi."""
from __future__ import annotations

import json
import re

SYSTEM_PROMPT = (
    "You are a meticulous archivist reading scanned pages of periodicals. "
    "You transcribe only what is visibly printed. You never guess, never infer, "
    "and never complete missing information from background knowledge. "
    "You always answer with a raw JSON array and nothing else."
)


def build_user_prompt(n_images: int) -> str:
    return f"""You are given {n_images} scanned image(s). Each image is a page from a DIFFERENT issue,
possibly a different publication, a different language and a different decade.
Images are provided in order; image #1 is the first one, image #{n_images} is the last.

For EACH image return one JSON object with exactly these keys:

  "index":            integer, 1..{n_images}, matching the order of the images
  "publication_title": the name of the periodical as printed on the page, or null
  "language":         ISO 639-1 code of the page language ("fr","de","pt","en","it","es"...), or null
  "is_cover":         true if this is a front page / cover, false otherwise
  "issue_number":     the issue number, DIGITS ONLY, as a string (e.g. "638", "2215"), or null
  "issue_suffix":     qualifier printed next to the number, lowercase, no spaces
                      (e.g. "bis", "special", "hs", "extra", "spezial"), or null
  "date_iso":         publication date as "YYYY-MM-DD", or null
  "date_raw":         the date exactly as printed, verbatim, or null
  "month_raw":        the month exactly as printed, verbatim (e.g. "mars", "Marz", "marco"), or null
  "year_printed":     true if the year is visibly printed on the page, false otherwise
  "page_number":      the page number printed on this page, DIGITS ONLY, as a string, or null
  "confidence":       your confidence for this image, a number between 0.0 and 1.0

STRICT RULES:
1. NEVER guess. If something is not clearly readable on the image, the value is null.
   A null is always better than an invented value.
2. If the year is NOT printed on the page, "date_iso" MUST be null, even when day and month are visible.
   Put whatever you can read into "date_raw".
3. Date ranges ("du 15 au 21 mars", "15.-21. Marz"): use the FIRST date for "date_iso".
4. Do NOT use the American month/day order. In European and Brazilian periodicals the day comes first.
   Whenever a month NAME is printed, trust the name, not the position of the numbers.
5. "issue_number" contains digits only. Any word or letter next to it goes to "issue_suffix",
   lowercase and without spaces. Do not translate it.
6. A cover may show no page number at all - then "page_number" is null.
7. Transcribe "publication_title" as printed, but in normal capitalisation
   (e.g. "France Football", not "FRANCE FOOTBALL").

Return ONLY a JSON array with exactly {n_images} objects, ordered by image index.
No markdown, no code fences, no commentary before or after."""


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_response(text: str, n_expected: int) -> list[dict]:
    """Wyciaga tablice obiektow z odpowiedzi modelu. Rzuca ValueError przy niepowodzeniu."""
    if not text or not text.strip():
        raise ValueError("pusta odpowiedz modelu")

    s = _FENCE.sub("", text.strip())

    data = None
    try:
        data = json.loads(s)
    except Exception:
        start, end = s.find("["), s.rfind("]")
        if start != -1 and end > start:
            try:
                data = json.loads(s[start:end + 1])
            except Exception:
                data = None
        if data is None:
            # ostatnia proba: pojedynczy obiekt zamiast tablicy
            start, end = s.find("{"), s.rfind("}")
            if start != -1 and end > start:
                try:
                    data = json.loads(s[start:end + 1])
                except Exception:
                    data = None

    if data is None:
        raise ValueError("odpowiedz nie zawiera poprawnego JSON")

    if isinstance(data, dict):
        for key in ("results", "items", "data", "pages"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON nie jest tablica obiektow")

    out: list[dict] = []
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"element {i} nie jest obiektem")
        try:
            idx = int(item.get("index", i))
        except Exception:
            idx = i
        item["index"] = idx
        out.append(item)

    if len(out) != n_expected:
        raise ValueError(f"model zwrocil {len(out)} rekordow zamiast {n_expected}")

    # uporzadkuj po index i sprawdz, czy to pelny zakres 1..N
    out.sort(key=lambda d: d["index"])
    if [d["index"] for d in out] != list(range(1, n_expected + 1)):
        raise ValueError("indeksy w odpowiedzi nie tworza pelnego zakresu")

    return out

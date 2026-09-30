"""Uniwersalny prompt (bez nazwy konkretnego tytulu) i odporny parser odpowiedzi.

Prompt ma dwie czesci:
  * stala  - format odpowiedzi i identyfikatory obrazow; na niej opiera sie
             przypisanie wynikow do plikow, wiec uzytkownik jej nie edytuje,
  * zasady - jak czytac date; domyslne ponizej, mozna je zastapic wlasnymi
             i dopisac uwagi o konkretnej kolekcji (zakladka "API i model").
"""
from __future__ import annotations

import json
import re

SYSTEM_PROMPT = (
    "You are a meticulous archivist reading scanned pages of periodicals. "
    "You transcribe only what is visibly printed. You never guess, never infer, "
    "and never complete missing information from background knowledge. "
    "You always answer with raw JSON and nothing else."
)

DEFAULT_RULES = """WHERE TO LOOK
- The publication date and issue number are printed in the masthead (the block with the
  title of the periodical, usually in the top third of a cover), in the running head
  (a narrow strip along the top edge of inner pages, next to the page number)
  or in the footer.
- Take the date ONLY from these fixed elements. NEVER from articles, advertisements,
  announcements, TV or event schedules, calendars, horoscopes or results tables.
  A calendar with a big day number is especially misleading - it is not the issue date.
- The date may be written in digits or in words. Look for both.

NEVER GUESS
- If something is not clearly readable, the value is null. A null is always better
  than an invented value. Do not complete missing digits.
- If the year is NOT printed on the page, "date_iso" MUST be null, even when day and
  month are visible. Put whatever you can read into "date_raw".
- Date ranges ("du 15 au 21 mars", "15.-21. Marz"): use the FIRST date for "date_iso".
- Judge every image on its own. Do not infer a date from other images in this request.

DAY / MONTH ORDER
- Whenever a month NAME is printed, trust the name, not the position of the numbers.
- For purely numeric dates use the convention of the publication's country
  (most of Europe and South America: day first; USA: month first).
- If the order is still ambiguous (e.g. "3-2-1955" with no other clue), set "date_iso"
  to null, list BOTH possibilities in "date_alternatives" and lower the confidence.

TRANSCRIBE, DO NOT TRANSLATE
- "date_raw", "month_raw" and "issue_suffix" are copied character by character in the
  language of the page. Do not translate month or weekday names, do not fix spelling,
  do not abbreviate. If the page says "26 de setembro de 1976", that is the date_raw.

ISSUE AND PAGE NUMBERS
- "issue_number" contains digits only. Any word or letter next to it goes to
  "issue_suffix", lowercase, without spaces (e.g. "bis", "special", "hs").
- The issue number is NOT the page number. If you only see a page number,
  "issue_number" is null.
- A cover may show no page number at all - then "page_number" is null.
- Transcribe "publication_title" as printed, but in normal capitalisation
  (e.g. "France Football", not "FRANCE FOOTBALL")."""


def rules_text(custom_rules: str | None = None, notes: str | None = None) -> str:
    """Zasady do promptu: wlasne albo domyslne, plus uwagi o kolekcji."""
    text = (custom_rules or "").strip() or DEFAULT_RULES
    notes = (notes or "").strip()
    if notes:
        text += "\n\nNOTES ABOUT THIS COLLECTION (from the user)\n" + notes
    return text


def _hints_text(ids: list[str], hints: dict | None) -> str:
    """Sekcja z danymi znanymi z nazw plikow (rok, numer, strona, tytul)."""
    if not hints:
        return ""
    lines = []
    for i in ids:
        h = hints.get(i) or {}
        parts = []
        if h.get("title"):
            parts.append(f'publication "{h["title"]}"')
        if h.get("year"):
            parts.append(f"year {h['year']}")
        if h.get("issue"):
            parts.append(f"issue {h['issue']}" + (f" {h['suffix']}" if h.get("suffix") else ""))
        if h.get("page"):
            parts.append(f"page {h['page']}" + (" (last page of the issue)" if h.get("ost") else ""))
        if parts:
            lines.append(f"  {i}: " + ", ".join(parts))
    if not lines:
        return ""
    return """
KNOWN FROM THE FILE NAMES (reliable - do not contradict them without clear evidence on the page)
""" + "\n".join(lines) + """

For these images your main task is the DAY and MONTH of the issue date printed on the page
(in the masthead on a front page, in the running head on inner pages, sometimes in the footer).
Because the year is known, return "date_iso" with that year whenever day and month are visible,
even if the year itself is not printed on this page - this overrides the rule about unprinted years.
If the page clearly shows a DIFFERENT year, return the year that is printed.
If the page shows two different dates that could each be the issue date (e.g. a scanning mix-up),
set "date_iso" to null and put both dates in "date_alternatives".
Fields known from the file name may be returned as null.
"""


def build_user_prompt(ids: list[str], custom_rules: str | None = None,
                      notes: str | None = None, hints: dict | None = None) -> str:
    n = len(ids)
    return f"""You are given {n} scanned image(s). Each image is a page from a DIFFERENT issue,
possibly a different publication, a different language and a different decade.

Each image has a white strip at the very top with an identifier in the form ### NNNNN ###.
The strip was added by the program and is NOT part of the periodical.
The identifiers in this request are: {", ".join(ids)}.

For EACH image return one JSON object with exactly these keys:

  "id":                the five digits from the white strip of THAT image (not its position)
  "publication_title": the name of the periodical as printed on the page, or null
  "language":          ISO 639-1 code of the page language ("fr","de","pt","en","it","es"...), or null
  "is_cover":          true if this is a front page / cover, false otherwise
  "issue_number":      the issue number, DIGITS ONLY, as a string (e.g. "638", "2215"), or null
  "issue_suffix":      qualifier printed next to the number, lowercase, no spaces, or null
  "date_iso":          publication date as "YYYY-MM-DD", or null
  "date_alternatives": list of possible "YYYY-MM-DD" dates when the date is ambiguous, otherwise []
  "date_raw":          the date exactly as printed, verbatim, or null
  "month_raw":         the month exactly as printed, verbatim (e.g. "mars", "Marz", "marco"), or null
  "year_printed":      true if the year is visibly printed on the page, false otherwise
  "page_number":       the page number printed on this page, DIGITS ONLY, as a string, or null
  "date_location":     where on the page the date was found (e.g. "masthead", "running head"), or null
  "confidence":        your confidence for this image, a number between 0.0 and 1.0

RULES

{rules_text(custom_rules, notes)}
{_hints_text(ids, hints)}
Return ONLY a JSON array with exactly {n} objects, one per identifier listed above.
No markdown, no code fences, no commentary before or after."""


# Schemat odpowiedzi dla Gemini (responseSchema) - wymusza poprawny JSON.
_STR = {"type": "STRING", "nullable": True}
RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "publication_title": _STR,
            "language": _STR,
            "is_cover": {"type": "BOOLEAN", "nullable": True},
            "issue_number": _STR,
            "issue_suffix": _STR,
            "date_iso": _STR,
            "date_alternatives": {"type": "ARRAY", "items": {"type": "STRING"}},
            "date_raw": _STR,
            "month_raw": _STR,
            "year_printed": {"type": "BOOLEAN", "nullable": True},
            "page_number": _STR,
            "date_location": _STR,
            "confidence": {"type": "NUMBER", "nullable": True},
        },
        "required": ["id", "date_iso", "issue_number", "confidence"],
    },
}


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _load_json(text: str):
    s = _FENCE.sub("", text.strip())
    try:
        return json.loads(s)
    except Exception:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        start, end = s.find(open_c), s.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(s[start:end + 1])
            except Exception:
                continue
    return None


def _norm_id(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(5) if digits else None


def parse_response(text: str, ids: list[str]) -> list[dict]:
    """Wyciaga wyniki z odpowiedzi i ustawia je w kolejnosci `ids`.

    Wyniki sa parowane po identyfikatorze z paska na obrazie, nie po kolejnosci.
    Rzuca ValueError, gdy czegos brakuje albo sie nie zgadza.
    """
    if not text or not text.strip():
        raise ValueError("pusta odpowiedz modelu")
    data = _load_json(text)
    if data is None:
        raise ValueError("odpowiedz nie zawiera poprawnego JSON")

    if isinstance(data, dict):
        for key in ("results", "items", "data", "pages", "images"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON nie jest tablica obiektow")

    by_id: dict[str, dict] = {}
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"element {i} nie jest obiektem")
        ident = _norm_id(item.get("id"))
        if ident is None and len(ids) == 1 and len(data) == 1:
            ident = ids[0]  # pojedynczy obraz - nie ma czego pomylic
        if ident is None:
            raise ValueError(f"element {i} nie ma identyfikatora")
        if ident in by_id:
            raise ValueError(f"identyfikator {ident} wystepuje dwa razy")
        item["id"] = ident
        by_id[ident] = item

    missing = [i for i in ids if i not in by_id]
    extra = [i for i in by_id if i not in ids]
    if missing or extra:
        raise ValueError(f"identyfikatory sie nie zgadzaja: brak {missing or '-'}, "
                         f"obce {extra or '-'}")
    return [by_id[i] for i in ids]

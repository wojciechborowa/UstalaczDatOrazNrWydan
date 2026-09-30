"""Wzorce nazw plikow wejsciowych - co da sie odczytac z samej nazwy.

Wzorzec to tekst z polami, np.:
    "{lp} - {tytul} - {rok}-{mm}-{dd} - {nr} - {str}"   (kolekcje stron)
    "{rok}-{nr}"                                       (np. "1956-962.pdf")

Pola:
    {lp}     liczba porzadkowa (cyfry)
    {tytul}  tytul gazety (dowolny tekst)
    {rok}    rok, 4 cyfry (albo "rrrr")
    {mm}     miesiac, cyfry (albo "mm" - nieznany)
    {dd}     dzien, cyfry (albo "dd" - nieznany)
    {nr}     numer wydania, cyfry, opcjonalnie z dowolnym dopiskiem (A, B, bis, s, special...)
    {str}    numer strony, cyfry, opcjonalnie z "-OST" (ostatnia strona)
    {*}      cokolwiek - pomijane

Spacje we wzorcu dopasowuja sie do dowolnej liczby spacji (takze zadnej).
Wielkosc liter nie ma znaczenia.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from naming import is_valid_date

FIELDS = {
    "lp": r"(?P<lp>\d{1,7})",
    "tytul": r"(?P<tytul>.+?)",
    "rok": r"(?P<rok>\d{4}|rrrr|yyyy|aaaa)",
    "mm": r"(?P<mm>\d{1,2}|mm|xx)",
    "dd": r"(?P<dd>\d{1,2}|dd|xx)",
    # numer + dowolny dopisek do nastepnego separatora: 023093A, 022025 bis, 010203s, 023100-2
    "nr": r"(?P<nr>\d{1,7}|n{3,7}|x{3,7})(?P<sfx>[^\\/]*?)",
    "str": r"(?P<str>\d{1,4})(?:[ -]?(?P<ost>ost))?",
    "*": r".*?",
}
_TOKEN = re.compile(r"\{([^{}]*)\}")


@dataclass
class Pattern:
    text: str
    title: str = ""          # tytul dla plikow z tego wzorca (gdy nie ma go w nazwie)
    builtin: bool = False
    keep_name: bool = False  # nazwa wyjsciowa = wejsciowa z uzupelniona data
    label: str = ""
    regex: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        self.regex = compile_pattern(self.text)
        # wzorzec kolekcji (lp + pelna data) - nazwa wyjsciowa = wejsciowa z uzupelniona data
        compact = re.sub(r"\s+", "", self.text.lower())
        if "{lp}" in compact and "{rok}-{mm}-{dd}" in compact:
            self.keep_name = True
        if not self.label:
            self.label = self.text


def compile_pattern(text: str) -> re.Pattern:
    """Wzorzec -> wyrazenie regularne. Rzuca ValueError przy blednym wzorcu."""
    parts, pos, used = [], 0, set()
    for m in _TOKEN.finditer(text):
        parts.append(_literal(text[pos:m.start()]))
        name = m.group(1).strip().lower()
        if name not in FIELDS:
            raise ValueError(f"nieznane pole {{{m.group(1)}}} - dozwolone: "
                             + ", ".join("{" + k + "}" for k in FIELDS))
        if name in used and name != "*":
            raise ValueError(f"pole {{{name}}} wystepuje dwa razy")
        used.add(name)
        parts.append(FIELDS[name])
        pos = m.end()
    parts.append(_literal(text[pos:]))
    if not used - {"*"}:
        raise ValueError("wzorzec nie ma zadnego pola, np. {rok} albo {nr}")
    # na koncu dopuszczamy " (2)" - dopisek programow przy kolizji nazw
    return re.compile("^" + "".join(parts) + r"(?:\s*\(\d+\))?$", re.IGNORECASE)


def _literal(s: str) -> str:
    out = []
    for chunk in re.split(r"(\s+)", s):
        if not chunk:
            continue
        out.append(r"\s*" if chunk.isspace() else re.escape(chunk))
    return "".join(out)


BUILTIN = [
    Pattern("{lp} - {tytul} - {rok}-{mm}-{dd} - {nr} - {str}", builtin=True, keep_name=True,
            label="kolekcja stron"),
    Pattern("{lp} - {tytul} - {rok}-{mm}-{dd} - {nr}", builtin=True, keep_name=True,
            label="kolekcja wydan"),
    Pattern("{tytul} - {rok}-{mm}-{dd} - {nr} - {str}", builtin=True, label="format wyjsciowy (strona)"),
    Pattern("{tytul} - {rok}-{mm}-{dd} - {nr}", builtin=True, label="format wyjsciowy"),
]


def user_patterns(items: list[dict]) -> list[Pattern]:
    out = []
    for it in items or []:
        try:
            out.append(Pattern(it.get("pattern", ""), title=it.get("title", "") or ""))
        except (ValueError, re.error):
            continue
    return out


def _int(v) -> int | None:
    return int(v) if v and str(v).isdigit() else None


def parse(filename: str, patterns: list[Pattern]) -> dict | None:
    """Dane z nazwy pliku wg pierwszego pasujacego wzorca albo None."""
    stem = Path(filename).stem
    for p in patterns:
        m = p.regex.match(stem)
        if not m:
            continue
        g = m.groupdict()
        year, month, day = _int(g.get("rok")), _int(g.get("mm")), _int(g.get("dd"))
        name_date = None
        if year and month and day:
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            name_date = iso if is_valid_date(iso) else None
        issue = g.get("nr")
        issue = str(int(issue)) if issue and issue.isdigit() else None
        sfx = (g.get("sfx") or "").strip(" -_") or None   # dopisek zostaje taki, jak w nazwie
        page = g.get("str")
        span = None
        if p.keep_name and g.get("rok") is not None and g.get("dd") is not None:
            s, e = m.start("rok"), m.end("dd")
            # data musi byc jednym ciaglym kawalkiem "rrrr-mm-dd"
            if re.fullmatch(r"\S{4}-\S{1,2}-\S{1,2}", stem[s:e]):
                span = (s, e)
        return {
            "pattern": p.label,
            "keep_name": bool(p.keep_name and span),
            "lp": g.get("lp"),
            "title": (g.get("tytul") or "").strip() or (p.title or None),
            "year": year,
            "month": month,
            "day": day,
            "issue": issue,
            "suffix": sfx,
            "page": str(int(page)) if page and page.isdigit() else None,
            "ost": bool(g.get("ost")),
            "date_span": span,
            "name_date": name_date,
        }
    return None


def preview(patterns: list[Pattern], names: list[str], limit: int = 3) -> dict:
    """Ile nazw pasuje do ktorego wzorca + kilka przykladow - do okna wzorcow."""
    counts: dict[str, int] = {}
    samples: dict[str, list[tuple[str, dict]]] = {}
    for n in names:
        d = parse(n, patterns)
        key = d["pattern"] if d else "(luzne - nie pasuje do zadnego)"
        counts[key] = counts.get(key, 0) + 1
        if d and len(samples.setdefault(key, [])) < limit:
            samples[key].append((n, d))
    return {"counts": counts, "samples": samples}

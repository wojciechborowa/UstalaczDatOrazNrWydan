"""Zapis i odczyt sesji roboczej (bez klucza API - ten zyje w osobnym pliku konfiguracyjnym)."""
from __future__ import annotations

import json
from pathlib import Path

from config import APP_VERSION

SESSION_KEYS = [
    "path", "orig_path", "old_name", "kind", "checked",
    "title", "language", "date_iso", "date_raw", "month_raw", "year_printed",
    "issue_number", "issue_suffix", "page_number", "is_cover", "confidence",
    "new_name", "status", "note", "raw", "model",
    "h2", "size", "name_data", "pattern", "name_complete",
    "ai", "ai_date", "ai_issue", "issues", "name_facts", "report_data",
]
# pola starszych wersji (kalendarz wydan, glosowanie, chronologia) - czytane tylko przy
# przenoszeniu starej sesji, potem znikaja
LEGACY_KEYS = ["cal_filled", "group_filled"]


def save_session(path: str | Path, records: list[dict], meta: dict) -> None:
    data = {
        "app_version": APP_VERSION,
        "meta": meta,
        "records": [{k: r.get(k) for k in SESSION_KEYS} for r in records],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def load_session(path: str | Path) -> tuple[list[dict], dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data.get("records", [])
    for r in records:
        for k in list(r):
            if k not in SESSION_KEYS and k not in LEGACY_KEYS:
                del r[k]
        for k in SESSION_KEYS:
            r.setdefault(k, None)
        r["checked"] = bool(r.get("checked"))
    return records, data.get("meta", {})

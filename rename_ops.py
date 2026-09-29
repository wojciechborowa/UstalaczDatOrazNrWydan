"""Zmiana nazw na dysku z pelnym logiem operacji i mozliwoscia cofniecia."""
from __future__ import annotations

import json
import time
from pathlib import Path

from config import RENAME_LOG_DIR, ensure_dirs
from naming import build_new_name, resolve_collision


def plan_renames(records: list[dict], title_override: str = "") -> tuple[list[dict], list[dict]]:
    """Zwraca (plan, pominiete). Nie dotyka dysku."""
    plan: list[dict] = []
    skipped: list[dict] = []
    taken: set[str] = set()

    for rec in records:
        src = Path(rec.get("path", ""))
        if not src.exists():
            skipped.append({"rec": rec, "reason": "plik nie istnieje"})
            continue
        name, err = build_new_name(rec, title_override)
        if not name:
            skipped.append({"rec": rec, "reason": err})
            continue
        dst = resolve_collision(src.parent / name, taken)
        if dst == src:
            skipped.append({"rec": rec, "reason": "nazwa juz poprawna"})
            continue
        taken.add(str(dst).lower())
        plan.append({"rec": rec, "src": str(src), "dst": str(dst)})

    return plan, skipped


def apply_renames(plan: list[dict], progress=None, should_stop=None) -> tuple[str, list[dict], list[dict]]:
    """Wykonuje plan. Zwraca (sciezka_logu, wykonane, bledy)."""
    ensure_dirs()
    done: list[dict] = []
    errors: list[dict] = []
    total = len(plan)

    for i, item in enumerate(plan, start=1):
        if should_stop and should_stop():
            break
        try:
            Path(item["src"]).rename(item["dst"])
            item["rec"]["path"] = item["dst"]
            item["rec"]["old_name"] = Path(item["dst"]).name
            item["rec"]["status"] = "zmieniono nazwe"
            done.append({"src": item["src"], "dst": item["dst"]})
        except Exception as exc:
            errors.append({"src": item["src"], "dst": item["dst"], "error": str(exc)})
            item["rec"]["status"] = "blad zmiany nazwy"
            item["rec"]["note"] = str(exc)
        if progress:
            progress(i, total)

    log_path = RENAME_LOG_DIR / f"rename_{time.strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text(json.dumps(
        {"ts": time.time(), "done": done, "errors": errors},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return str(log_path), done, errors


def list_logs() -> list[Path]:
    ensure_dirs()
    return sorted(RENAME_LOG_DIR.glob("rename_*.json"), reverse=True)


def undo_renames(log_path: str | Path, records: list[dict] | None = None,
                 progress=None) -> tuple[int, list[dict]]:
    """Cofa operacje z logu. Zwraca (liczba_cofnietych, bledy)."""
    data = json.loads(Path(log_path).read_text(encoding="utf-8"))
    entries = list(reversed(data.get("done", [])))
    by_path = {}
    if records:
        by_path = {str(r.get("path")): r for r in records}

    restored, errors = 0, []
    total = len(entries)
    for i, item in enumerate(entries, start=1):
        src, dst = Path(item["src"]), Path(item["dst"])
        try:
            if not dst.exists():
                errors.append({"dst": str(dst), "error": "plik nie istnieje (juz cofniety?)"})
            elif src.exists():
                errors.append({"dst": str(dst), "error": f"nazwa docelowa zajeta: {src.name}"})
            else:
                dst.rename(src)
                restored += 1
                rec = by_path.get(str(dst))
                if rec is not None:
                    rec["path"] = str(src)
                    rec["old_name"] = src.name
                    rec["status"] = "cofnieto zmiane nazwy"
        except Exception as exc:
            errors.append({"dst": str(dst), "error": str(exc)})
        if progress:
            progress(i, total)

    return restored, errors

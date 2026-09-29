"""Cache wynikow po hashu pliku - pozwala wznowic przerwany przebieg bez marnowania limitu."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

from config import CACHE_DB, ensure_dirs

_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    file_hash TEXT NOT NULL,
    model     TEXT NOT NULL,
    data      TEXT NOT NULL,
    ts        REAL NOT NULL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (file_hash, model)
);
"""


def file_hash(path: str | Path, chunk: int = 262144) -> str:
    """Szybki, stabilny odcisk pliku: rozmiar + poczatek + koniec.

    Pelny SHA-256 przy 2000 skanach po kilkadziesiat MB trwalby minuty,
    a do celow cache'u ta wersja jest w zupelnosci wystarczajaca.
    """
    p = Path(path)
    size = p.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with p.open("rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()


def _conn() -> sqlite3.Connection:
    ensure_dirs()
    c = sqlite3.connect(CACHE_DB, timeout=30)
    c.execute(_SCHEMA)
    return c


def get(file_hash_: str, model: str) -> dict | None:
    with _lock:
        c = _conn()
        try:
            row = c.execute(
                "SELECT data FROM results WHERE file_hash=? AND model=?",
                (file_hash_, model),
            ).fetchone()
        finally:
            c.close()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def put(file_hash_: str, model: str, data: dict) -> None:
    with _lock:
        c = _conn()
        try:
            c.execute(
                "INSERT OR REPLACE INTO results (file_hash, model, data) VALUES (?,?,?)",
                (file_hash_, model, json.dumps(data, ensure_ascii=False)),
            )
            c.commit()
        finally:
            c.close()


def clear() -> int:
    with _lock:
        c = _conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM results").fetchone()[0]
            c.execute("DELETE FROM results")
            c.commit()
            return int(n)
        finally:
            c.close()


def count() -> int:
    with _lock:
        c = _conn()
        try:
            return int(c.execute("SELECT COUNT(*) FROM results").fetchone()[0])
        finally:
            c.close()

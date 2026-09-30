"""Aktualizacja programu: z pliku ZIP albo z repozytorium na GitHubie.

Paczka to ZIP z plikami programu (moga byc w podfolderze). Opcjonalnie zawiera
update.json: {"version": "2.1", "notes": "co sie zmienilo"}.

Przed podmiana program kopiuje zastepowane pliki do folderu kopii zapasowych,
wiec kazda aktualizacje mozna cofnac. Podmieniane sa tylko pliki programu
(.py, .md, .txt, .json, .bat, .cmd) - nic poza folderem programu.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath

import requests

from config import APP_DIR, APP_VERSION

APP_ROOT = Path(__file__).resolve().parent
BACKUP_DIR = APP_DIR / "backups"
ALLOWED_EXT = {".py", ".md", ".txt", ".json", ".bat", ".cmd"}
SKIP_NAMES = {"update.json"}
DEFAULT_REPO = "wojciechborowa/UstalaczDatOrazNrWydan"
DEFAULT_BRANCH = "claude/program-fix-izr5og"


class UpdateError(RuntimeError):
    pass


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0")))


def _read_zip(data: bytes) -> dict[str, bytes]:
    """Pliki programu z paczki: {sciezka wzgledna: zawartosc}. Wspolny folder na
    poczatku (np. 'czytnik_wydan/' albo 'Repo-galaz/') jest pomijany."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"to nie jest poprawny plik ZIP ({exc})")
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        raise UpdateError("paczka jest pusta")
    for n in names:   # sprawdzamy przed zdjeciem wspolnego folderu
        p = PurePosixPath(n.replace("\\", "/"))
        if p.is_absolute() or ".." in p.parts or ":" in n:
            raise UpdateError(f"niebezpieczna sciezka w paczce: {n}")
    parts = [PurePosixPath(n).parts for n in names]
    prefix = 0
    # zdejmujemy wspolny folder, jesli wszystkie pliki w nim siedza
    while all(len(p) > prefix + 1 for p in parts) and len({p[prefix] for p in parts}) == 1:
        prefix += 1
    out = {}
    for n, p in zip(names, parts):
        rel = PurePosixPath(*p[prefix:])
        if rel.is_absolute() or ".." in rel.parts:
            raise UpdateError(f"niebezpieczna sciezka w paczce: {n}")
        out[str(rel)] = zf.read(n)
    return out


def inspect(data: bytes) -> dict:
    """Co zmieni paczka: wersja, opis, pliki nowe i zmienione (bez zapisu na dysk)."""
    files = _read_zip(data)
    meta = {}
    if "update.json" in files:
        try:
            meta = json.loads(files["update.json"].decode("utf-8"))
        except Exception:
            meta = {}
    changed, added, ignored = [], [], []
    for rel, content in sorted(files.items()):
        path = APP_ROOT / rel
        if rel in SKIP_NAMES or "__pycache__" in rel or rel.startswith("."):
            continue
        if path.suffix.lower() not in ALLOWED_EXT:
            ignored.append(rel)
            continue
        if not path.exists():
            added.append(rel)
        elif path.read_bytes() != content:
            changed.append(rel)
    if not any(r.endswith(".py") for r in files):
        raise UpdateError("w paczce nie ma plikow programu (.py)")
    version = meta.get("version") or _version_from(files.get("config.py"))
    return {"files": files, "changed": changed, "added": added, "ignored": ignored,
            "version": version, "notes": meta.get("notes", "")}


def _version_from(config_py: bytes | None) -> str:
    if not config_py:
        return ""
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', config_py.decode("utf-8", "replace"))
    return m.group(1) if m else ""


def apply(info: dict) -> Path:
    """Podmienia pliki. Zwraca folder kopii zapasowej (do cofniecia)."""
    todo = info["changed"] + info["added"]
    if not todo:
        raise UpdateError("paczka nie zmienia zadnego pliku - masz juz te wersje")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{stamp}_v{APP_VERSION}"
    backup.mkdir(parents=True, exist_ok=True)
    for rel in info["changed"]:
        dst = backup / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(APP_ROOT / rel, dst)
    (backup / "_backup.json").write_text(json.dumps({
        "from_version": APP_VERSION, "to_version": info.get("version", ""),
        "changed": info["changed"], "added": info["added"], "time": stamp,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    written = []
    try:
        for rel in todo:
            dst = APP_ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".new")
            tmp.write_bytes(info["files"][rel])
            tmp.replace(dst)     # podmiana atomowa - plik nigdy nie jest w polowie zapisany
            written.append(rel)
    except Exception as exc:
        _restore(backup)
        raise UpdateError(f"blad zapisu ({exc}) - przywrocono poprzednia wersje")
    return backup


def backups() -> list[Path]:
    if not BACKUP_DIR.exists():
        return []
    return sorted((p for p in BACKUP_DIR.iterdir() if (p / "_backup.json").exists()), reverse=True)


def _restore(backup: Path) -> dict:
    meta = json.loads((backup / "_backup.json").read_text(encoding="utf-8"))
    for rel in meta.get("changed", []):
        src = backup / rel
        if src.exists():
            shutil.copy2(src, APP_ROOT / rel)
    for rel in meta.get("added", []):
        p = APP_ROOT / rel
        if p.exists():
            p.unlink()
    return meta


def rollback() -> dict:
    """Cofa ostatnia aktualizacje."""
    lst = backups()
    if not lst:
        raise UpdateError("nie ma zadnej kopii zapasowej do przywrocenia")
    meta = _restore(lst[0])
    shutil.rmtree(lst[0], ignore_errors=True)
    return meta


# ------------------------------------------------------------------ GitHub
def remote_changes(repo: str, branch: str, limit: int = 8) -> list[str]:
    """Opisy ostatnich zmian (pierwsze linie commitow) - do pokazania przed aktualizacja."""
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/commits",
                         params={"sha": branch, "per_page": limit}, timeout=30)
        r.raise_for_status()
        return [c["commit"]["message"].splitlines()[0] for c in r.json()]
    except Exception:
        return []


def download(repo: str, branch: str) -> bytes:
    url = f"https://codeload.github.com/{repo}/zip/refs/heads/{branch}"
    r = requests.get(url, timeout=120)
    if r.status_code == 404:
        raise UpdateError(f"nie znaleziono repozytorium albo galezi: {repo} / {branch}")
    r.raise_for_status()
    return r.content

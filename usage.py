"""Wlasne liczniki zuzycia limitow: zapytania na minute i na dzien, tokeny na minute.

Program nie pyta konta u dostawcy - liczy tylko to, co sam wyslal (zapytania innych
programow na tym samym kluczu nie sa tu widoczne). Liczniki sa osobne dla kazdej
trojki dostawca + model + klucz (klucz rozpoznawany po skrocie, nie jest zapisywany).

Limit dzienny Gemini odnawia sie o polnocy czasu pacyficznego (USA), OpenRoutera
o polnocy UTC. Limity sa edytowalne - wartosci domyslne ponizej sa orientacyjne,
bo dostawcy je zmieniaja, a API ich nie podaje.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import threading
import time
from pathlib import Path

from config import APP_DIR

USAGE_FILE = APP_DIR / "usage.json"
WINDOW = 60.0

# (dostawca, fragment nazwy modelu, limity) - pierwszy pasujacy wygrywa
DEFAULT_LIMITS = [
    ("gemini", "flash-lite", {"rpm": 15, "tpm": 250000, "rpd": 1000}),
    ("gemini", "flash", {"rpm": 10, "tpm": 250000, "rpd": 250}),
    ("gemini", "pro", {"rpm": 5, "tpm": 250000, "rpd": 100}),
    ("gemini", "", {"rpm": 10, "tpm": 250000, "rpd": 250}),
    ("openrouter", "", {"rpm": 20, "tpm": 0, "rpd": 50}),
]
DEFAULT_TOKENS_PER_IMAGE = 1600.0


def default_limits(provider: str, model: str) -> dict:
    m = (model or "").lower()
    for prov, frag, lim in DEFAULT_LIMITS:
        if prov == provider and frag in m:
            return dict(lim)
    return {"rpm": 10, "tpm": 0, "rpd": 0}


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256((api_key or "").strip().encode()).hexdigest()[:10]


# ------------------------------------------------------------------ doba limitu
def _nth_sunday(year: int, month: int, n: int) -> _dt.date:
    d = _dt.date(year, month, 1)
    d += _dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=n - 1)


def pacific_offset(utc: _dt.datetime) -> _dt.timedelta:
    """Przesuniecie czasu pacyficznego (PST -8 / PDT -7) wg regul USA od 2007 r.
    Liczone recznie - Windows nie ma bazy stref czasowych bez dodatkowego pakietu."""
    y = utc.year
    start = _dt.datetime.combine(_nth_sunday(y, 3, 2), _dt.time(10))   # 2:00 PST = 10:00 UTC
    end = _dt.datetime.combine(_nth_sunday(y, 11, 1), _dt.time(9))     # 2:00 PDT = 9:00 UTC
    naive = utc.replace(tzinfo=None)
    return _dt.timedelta(hours=-7 if start <= naive < end else -8)


def day_window(provider: str, now: float | None = None) -> tuple[str, float]:
    """(klucz doby limitu, chwila najblizszego odnowienia jako timestamp)."""
    now = time.time() if now is None else now
    utc = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    off = pacific_offset(utc) if provider == "gemini" else _dt.timedelta(0)
    local = utc + off
    nxt = _dt.datetime.combine(local.date() + _dt.timedelta(days=1), _dt.time(0))
    reset_utc = nxt - off
    reset_ts = reset_utc.replace(tzinfo=_dt.timezone.utc).timestamp()
    return local.date().isoformat(), reset_ts


def fmt_reset(ts: float) -> str:
    t = _dt.datetime.fromtimestamp(ts)
    today = _dt.date.today()
    when = "dzis" if t.date() == today else "jutro" if t.date() == today + _dt.timedelta(days=1) \
        else t.strftime("%Y-%m-%d")
    return f"{when} o {t.strftime('%H:%M')}"


class DailyLimitReached(RuntimeError):
    """Licznik programu doszedl do limitu dziennego - dalsze zapytania i tak by odpadly."""


# ------------------------------------------------------------------- liczniki
class Usage:
    def __init__(self, path: str | Path = USAGE_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    @staticmethod
    def key(provider: str, model: str, api_key: str) -> str:
        return f"{provider}|{model}|{key_fingerprint(api_key)}"

    def _entry(self, key: str, now: float) -> dict:
        provider = key.split("|", 1)[0]
        day, reset = day_window(provider, now)
        e = self.data.setdefault(key, {})
        if e.get("day") != day:
            tpi = e.get("tok_per_img")
            e.clear()
            e.update(day=day, requests=0, tokens=0, images=0, recent=[])
            if tpi:
                e["tok_per_img"] = tpi
        e["reset"] = reset
        e["recent"] = [x for x in e.get("recent", []) if now - x[0] < WINDOW]
        return e

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def estimate(self, key: str, n_images: int) -> int:
        with self._lock:
            tpi = (self.data.get(key) or {}).get("tok_per_img") or DEFAULT_TOKENS_PER_IMAGE
        return int(tpi * max(1, n_images))

    def acquire(self, key: str, limits: dict, n_images: int, should_stop=None, on_wait=None):
        """Czeka, az limity na minute pozwola wyslac zapytanie, i je rejestruje.

        Zwraca uchwyt dla settle() albo None, gdy przerwano. Rzuca DailyLimitReached,
        gdy licznik dzienny doszedl do limitu."""
        rpm, tpm, rpd = (int(limits.get(k) or 0) for k in ("rpm", "tpm", "rpd"))
        est = self.estimate(key, n_images)
        while True:
            if should_stop and should_stop():
                return None
            with self._lock:
                now = time.time()
                e = self._entry(key, now)
                if rpd and e["requests"] >= rpd:
                    raise DailyLimitReached(
                        f"Licznik programu: wykorzystano {e['requests']}/{rpd} zapytan dziennie "
                        f"dla tego modelu i klucza. Limit odnowi sie {fmt_reset(e['reset'])}. "
                        "Mozesz tez wybrac inny model albo dostawce. Jesli limit w programie jest "
                        "nizszy niz faktyczny, popraw go w zakladce 'API i model'.")
                wait = 0.0
                recent = e["recent"]
                if rpm and len(recent) >= rpm:
                    wait = max(wait, WINDOW - (now - recent[len(recent) - rpm][0]))
                if tpm and recent:
                    used = sum(x[1] for x in recent)
                    if used + est > tpm:
                        # czekamy, az z okna wypadnie tyle, ile trzeba
                        need, acc = used + est - tpm, 0
                        for t, tok in recent:
                            acc += tok
                            if acc >= need:
                                wait = max(wait, WINDOW - (now - t))
                                break
                if wait <= 0:
                    entry = [now, est]
                    recent.append(entry)
                    e["requests"] += 1
                    e["images"] = e.get("images", 0) + n_images
                    self._save()
                    return (key, now, n_images)
            wait += 0.1
            if on_wait:
                on_wait(wait)
            end = time.time() + wait
            while time.time() < end:
                if should_stop and should_stop():
                    return None
                time.sleep(0.2)

    def settle(self, handle, tokens: int | None) -> None:
        """Po odpowiedzi: szacunek tokenow zamieniamy na faktyczne zuzycie."""
        if not handle:
            return
        key, ts, n_images = handle
        with self._lock:
            e = self._entry(key, time.time())
            for x in e["recent"]:
                if x[0] == ts:
                    if tokens:
                        x[1] = int(tokens)
                    break
            if tokens:
                e["tokens"] = e.get("tokens", 0) + int(tokens)
                per = tokens / max(1, n_images)
                old = e.get("tok_per_img")
                e["tok_per_img"] = round(per if not old else old * 0.7 + per * 0.3, 1)
            self._save()

    def snapshot(self, key: str) -> dict:
        with self._lock:
            now = time.time()
            e = self._entry(key, now)
            return {
                "day_requests": e["requests"], "day_tokens": e.get("tokens", 0),
                "day_images": e.get("images", 0),
                "min_requests": len(e["recent"]), "min_tokens": sum(x[1] for x in e["recent"]),
                "reset": e["reset"], "tok_per_img": e.get("tok_per_img"),
            }


def describe(snap: dict, limits: dict) -> str:
    """Jedna linijka do zakladki API i paska stanu."""
    rpm, tpm, rpd = (int(limits.get(k) or 0) for k in ("rpm", "tpm", "rpd"))
    parts = [f"dzis {snap['day_requests']}" + (f"/{rpd}" if rpd else "") + " zapytan",
             f"ostatnia minuta {snap['min_requests']}" + (f"/{rpm}" if rpm else "")]
    if tpm:
        parts.append(f"tokeny/min {snap['min_tokens'] // 1000}k/{tpm // 1000}k")
    if snap.get("day_tokens"):
        parts.append(f"tokeny dzis {snap['day_tokens'] // 1000}k")
    parts.append(f"limit dzienny odnowi sie {fmt_reset(snap['reset'])}")
    return "  ·  ".join(parts)


def advice(snap: dict, limits: dict, n_files: int, batch: int) -> list[str]:
    """Podpowiedzi przed startem odczytu: czy limit dzienny wystarczy i ile to potrwa."""
    rpm, tpm, rpd = (int(limits.get(k) or 0) for k in ("rpm", "tpm", "rpd"))
    batch = max(1, batch)
    need = math.ceil(n_files / batch)
    out = [f"Zapytan do wyslania: ok. {need} ({n_files} skanow po {batch})."]
    if rpd:
        left = max(0, rpd - snap["day_requests"])
        out.append(f"Limit dzienny wg licznika programu: wykorzystano {snap['day_requests']}/{rpd}, "
                   f"zostalo {left} zapytan (ok. {left * batch} skanow).")
        if need > left:
            out.append(f"UWAGA: dzis wystarczy na ok. {left * batch} z {n_files} skanow. "
                       f"Reszta po odnowieniu limitu ({fmt_reset(snap['reset'])}) - odczyt zatrzyma "
                       "sie sam, a zaznaczone zostana tylko nieodczytane pliki do wznowienia.")
    per_req = int((snap.get("tok_per_img") or DEFAULT_TOKENS_PER_IMAGE) * batch)
    eff = rpm or 60
    if tpm:
        if per_req > tpm:
            out.append(f"UWAGA: jedno zapytanie (~{per_req // 1000}k tokenow) przekracza limit "
                       f"tokenow na minute ({tpm // 1000}k) - zmniejsz liczbe skanow w zapytaniu.")
        else:
            eff = min(eff, max(1, tpm // per_req))
    minutes = need / max(1, eff)
    if minutes >= 1:
        out.append(f"Szacowany czas przy limicie {eff} zapytan/min: ok. {int(math.ceil(minutes))} min.")
    return out

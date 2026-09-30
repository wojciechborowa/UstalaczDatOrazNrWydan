"""Watek roboczy: renderowanie, wysylka batchy, cache, pauza/stop, postep."""
from __future__ import annotations

import json
import threading
import time

import cache_db
import render
from config import BATCH_SIZE
from ai_base import BaseClient, FatalApiError

FIELDS = ("title", "language", "date_iso", "date_raw", "month_raw", "year_printed",
          "issue_number", "issue_suffix", "page_number", "is_cover", "confidence")


def _apply(rec: dict, item: dict, model: str, raw: str) -> None:
    """Wynik AI -> rekord. Dane z nazwy pliku (tytul, numer, strona, rok) maja pierwszenstwo."""
    nd = rec.get("name_data") or {}
    rec["title"] = nd.get("title") or item.get("publication_title")
    rec["language"] = item.get("language")
    rec["date_iso"] = item.get("date_iso")
    rec["date_raw"] = item.get("date_raw")
    rec["month_raw"] = item.get("month_raw")
    rec["year_printed"] = item.get("year_printed")
    if nd.get("issue"):
        rec["issue_number"], rec["issue_suffix"] = nd["issue"], nd.get("suffix")
    else:
        rec["issue_number"] = item.get("issue_number")
        rec["issue_suffix"] = item.get("issue_suffix")
    rec["page_number"] = nd.get("page") or item.get("page_number")
    rec["is_cover"] = item.get("is_cover")
    try:
        rec["confidence"] = round(float(item.get("confidence")), 2)
    except Exception:
        rec["confidence"] = None
    rec["model"] = model
    rec["raw"] = raw
    for flag in ("year_mismatch", "name_unconfirmed", "vote_conflict"):
        rec[flag] = False

    alts = [a for a in (item.get("date_alternatives") or []) if a]
    notes = []
    missing = []
    if not rec.get("date_iso"):
        missing.append("data")
    if not rec.get("issue_number") and not nd.get("keep_name"):
        missing.append("nr wydania")
    if missing:
        notes.append("nie odczytano: " + ", ".join(missing))
    if len(alts) > 1:
        # data niejednoznaczna albo dwie rozne daty na skanie - do sprawdzenia recznego
        notes.insert(0, "data niejednoznaczna: " + " | ".join(alts))
        if rec["confidence"] is not None:
            rec["confidence"] = min(rec["confidence"], 0.5)
    year = nd.get("year")
    if year and rec.get("date_iso") and not str(rec["date_iso"]).startswith(str(year)):
        rec["year_mismatch"] = True
        notes.append(f"rok na skanie ({rec['date_iso'][:4]}) inny niz w nazwie pliku ({year})")
    rec["status"] = "brak danych" if missing else "odczytano"

    # data zapisana w nazwie pliku (np. przez inny program) - AI ma ja potwierdzic
    name_date = rec.get("name_date")
    if name_date:
        if rec.get("date_iso") == name_date:
            rec["status"] = "potwierdzone"
            rec["confidence"] = max(rec["confidence"] or 0.0, 0.95)
            notes.insert(0, "data z nazwy potwierdzona odczytem AI")
        elif rec.get("date_iso"):
            rec["vote_conflict"] = True
            notes.insert(0, f"w nazwie pliku {name_date}, AI odczytalo {rec['date_iso']}")
        else:
            rec["date_iso"] = name_date
            rec["name_unconfirmed"] = True
            rec["status"] = "odczytano"
            notes.insert(0, "AI nie odczytalo daty - zostaje data z nazwy (niepotwierdzona)")
    rec["note"] = "; ".join(notes)


def hints_for(rec: dict) -> dict | None:
    """Co wiadomo z nazwy pliku - trafia do promptu (bez daty: odczyt ma byc niezalezny)."""
    nd = rec.get("name_data") or {}
    h = {k: nd.get(k) for k in ("title", "year", "issue", "suffix", "page", "ost") if nd.get(k)}
    return h or None


class ReadWorker(threading.Thread):
    """Przetwarza liste rekordow. Komunikuje sie z GUI przez kolejke zdarzen."""

    def __init__(self, records: list[dict], client: BaseClient, model: str, queue,
                 batch_size: int = BATCH_SIZE, use_cache: bool = True,
                 rules: str | None = None, notes: str | None = None,
                 detail: bool = False):
        super().__init__(daemon=True)
        self.records = records
        self.model = model
        self.queue = queue
        self.batch_size = max(1, int(batch_size))
        self.use_cache = use_cache
        self.client = client
        self.rules = rules
        self.detail = detail
        if detail:
            extra = ("Each image shows, below the identifier strip, first an ENLARGED top part "
                     "of the page (where the masthead with date and issue number usually is), "
                     "then a grey line, then the WHOLE page. Both show the same single page.")
            notes = (notes + "\n" + extra) if notes else extra
        self.notes = notes
        self._seq = 0

        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()
        self._pause_evt.set()  # ustawiona == dziala

        self.started_at = 0.0
        self.paused_total = 0.0

    # ------------------------------------------------------------- sterowanie
    def stop(self) -> None:
        self._stop_evt.set()
        self._pause_evt.set()

    def pause(self) -> None:
        self._pause_evt.clear()

    def resume(self) -> None:
        self._pause_evt.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_evt.is_set()

    def should_stop(self) -> bool:
        return self._stop_evt.is_set()

    def _gate(self) -> None:
        """Blokuje watek na czas pauzy i doliczaja ja do czasu, zeby ETA nie klamalo."""
        if self._pause_evt.is_set():
            return
        t0 = time.time()
        self.emit("paused", {})
        while not self._pause_evt.is_set():
            if self._stop_evt.is_set():
                return
            time.sleep(0.15)
        self.paused_total += time.time() - t0
        self.emit("resumed", {})

    def _image(self, rec: dict) -> tuple[str, str]:
        """(identyfikator, JPEG w base64) - identyfikator jest wypisany na obrazie."""
        self._seq += 1
        ident = f"{self._seq:05d}"
        rec["_ident"] = ident
        return ident, render.to_jpeg_b64(rec["path"], ident, detail=self.detail)

    def _ask(self, images, recs, on_wait=None):
        hints = {r["_ident"]: h for r in recs for h in [hints_for(r)] if h}
        return self.client.read_batch(images, self.model, self.rules, self.notes,
                                      should_stop=self.should_stop, on_wait=on_wait,
                                      hints=hints or None)

    def _cache_key(self, rec: dict) -> str:
        # odczyt z danymi z nazwy pliku to inne pytanie niz odczyt "na slepo"
        return self.model + ("|nazwa" if hints_for(rec) else "")

    def _store(self, rec: dict, item: dict) -> str:
        """Zapis do cache - tylko wynik tego pliku, bez surowej odpowiedzi calej paczki."""
        raw = json.dumps(item, ensure_ascii=False, indent=1)
        if self.use_cache and rec.get("_hash"):
            try:
                cache_db.put(rec["_hash"], self._cache_key(rec), item)
            except Exception:
                pass
        return raw

    def emit(self, kind: str, data: dict) -> None:
        self.queue.put((kind, data))

    # ------------------------------------------------------------------- bieg
    def run(self) -> None:
        self.started_at = time.time()
        pending: list[dict] = []
        from_cache = 0

        # 1) najpierw cache - nie zuzywa limitu
        for rec in self.records:
            if self._stop_evt.is_set():
                break
            if self.use_cache:
                try:
                    h = cache_db.file_hash(rec["path"])
                    rec["_hash"] = h
                    hit = cache_db.get(h, self._cache_key(rec))
                except Exception:
                    hit = None
                if hit:
                    hit.pop("_raw", None)   # starsze wpisy trzymaly cala odpowiedz paczki
                    _apply(rec, hit, self.model, json.dumps(hit, ensure_ascii=False, indent=1))
                    rec["status"] = rec["status"] + " (cache)"
                    from_cache += 1
                    self.emit("record", {"rec": rec})
                    continue
            pending.append(rec)

        total = len(pending)
        batches = [pending[i:i + self.batch_size] for i in range(0, total, self.batch_size)]
        self.emit("plan", {"total_files": len(self.records), "from_cache": from_cache,
                           "to_send": total, "batches": len(batches)})

        done_files = 0
        for bi, batch in enumerate(batches, start=1):
            self._gate()
            if self._stop_evt.is_set():
                break

            self.emit("batch_start", {"index": bi, "count": len(batches),
                                      "files": [r["old_name"] for r in batch]})

            # render
            images, ok_batch = [], []
            for rec in batch:
                if self._stop_evt.is_set():
                    break
                try:
                    images.append(self._image(rec))
                    ok_batch.append(rec)
                except Exception as exc:
                    rec["status"] = "blad odczytu pliku"
                    rec["note"] = str(exc)
                    done_files += 1
                    self.emit("record", {"rec": rec})
            if not ok_batch or self._stop_evt.is_set():
                continue

            try:
                items, raw = self._ask(
                    images, ok_batch,
                    on_wait=lambda d, a: self.emit("waiting", {"delay": d, "attempt": a}))
            except FatalApiError as exc:
                self.emit("fatal", {"error": str(exc)})
                break
            except Exception as exc:
                # caly batch idzie do ponowienia pojedynczo - najczestsza przyczyna
                # bledu to przesuniecie odpowiedzi w duzej paczce
                self.emit("log", {"text": f"Batch {bi}: {exc} -> ponawiam pojedynczo"})
                for rec in ok_batch:
                    self._gate()
                    if self._stop_evt.is_set():
                        break
                    try:
                        one, _raw1 = self._ask([self._image(rec)], [rec])
                        _apply(rec, one[0], self.model, self._store(rec, one[0]))
                    except FatalApiError as exc2:
                        self.emit("fatal", {"error": str(exc2)})
                        self._stop_evt.set()
                        break
                    except Exception as exc2:
                        rec["status"] = "blad"
                        rec["note"] = str(exc2)
                        rec["raw"] = ""
                    done_files += 1
                    self.emit("record", {"rec": rec})
                    self.emit("progress", {"done": done_files, "total": total,
                                           "elapsed": self.elapsed()})
                continue

            for rec, item in zip(ok_batch, items):
                _apply(rec, item, self.model, self._store(rec, item))
                done_files += 1
                self.emit("record", {"rec": rec})

            self.emit("progress", {"done": done_files, "total": total,
                                   "elapsed": self.elapsed()})

        self.emit("finished", {"stopped": self._stop_evt.is_set(),
                               "done": done_files, "total": total,
                               "from_cache": from_cache,
                               "elapsed": self.elapsed()})

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, time.time() - self.started_at - self.paused_total)

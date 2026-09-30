"""Watek roboczy: renderowanie, wysylka batchy, cache, pauza/stop, postep."""
from __future__ import annotations

import json
import threading
import time

import cache_db
import render
import assess
from config import BATCH_SIZE
from ai_base import BaseClient, FatalApiError
from usage import DailyLimitReached

def _apply(rec: dict, item: dict, model: str, raw: str, mode: str) -> None:
    """Wynik AI -> rekord. Odpowiedz modelu zostaje w rec['ai'], a pola rekordu
    ustala assess.evaluate (porownanie z nazwa pliku i raportem)."""
    item = {k: v for k, v in item.items() if k != "id"}
    rec["ai"] = item
    rec["model"] = model
    rec["raw"] = raw
    rec["status"] = assess.STATUS_OK
    assess.evaluate(rec, mode)


def hints_for(rec: dict, mode: str) -> dict | None:
    """Co wiadomo z nazwy pliku - trafia do promptu tylko w kolekcji stron (tam AI ustala
    dzien i miesiac, a reszta jest w nazwie). W kolekcji wydan odczyt jest niezalezny,
    zeby porownanie z nazwa pliku cos znaczylo."""
    if mode != assess.MODE_PAGES:
        return None
    nd = rec.get("name_data") or {}
    h = {k: nd.get(k) for k in ("title", "year", "issue", "suffix", "page", "ost") if nd.get(k)}
    if not h.get("year"):
        y = (rec.get("name_facts") or {}).get("year")
        if y:
            h["year"] = y
    return h or None


MODE_NOTES = {
    assess.MODE_PAGES: (
        "Each image is a single page or a two-page spread (two facing pages scanned together) "
        "from INSIDE an issue, only rarely the cover. The issue date is usually in the running "
        "head along the top edge or in the footer of either page. Your main task is the DAY and "
        "the MONTH of that date."),
}


class ReadWorker(threading.Thread):
    """Przetwarza liste rekordow. Komunikuje sie z GUI przez kolejke zdarzen."""

    def __init__(self, records: list[dict], client: BaseClient, model: str, queue,
                 batch_size: int = BATCH_SIZE, use_cache: bool = True,
                 rules: str | None = None, notes: str | None = None,
                 detail: bool = False, mode: str = assess.MODE_ISSUES, page: int = 0):
        super().__init__(daemon=True)
        self.records = records
        self.model = model
        self.queue = queue
        self.batch_size = max(1, int(batch_size))
        self.use_cache = use_cache
        self.client = client
        self.rules = rules
        self.detail = detail
        self.mode = mode
        # strona PDF-a wysylana do AI (od 0) - tylko w kolekcji wydan
        self.page = max(0, int(page or 0)) if mode == assess.MODE_ISSUES else 0
        extras = []
        if MODE_NOTES.get(mode):
            extras.append(MODE_NOTES[mode])
        if self.page:
            extras.append(f"Each image is page {self.page + 1} of its issue (chosen by the user "
                          "because the date and issue number are printed there), so it is not "
                          "necessarily the cover.")
        if detail:
            extras.append("Each image shows, below the identifier strip, first an ENLARGED top part "
                          "of the page (where the masthead with date and issue number usually is), "
                          "then a grey line, then the WHOLE page. Both show the same single page.")
        for extra in extras:
            notes = (notes + "\n" + extra) if notes else extra
        self.notes = notes
        self.daily_limit = False
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
        return ident, render.to_jpeg_b64(rec["path"], ident, detail=self.detail, page=self.page)

    def _ask(self, images, recs, on_wait=None):
        hints = {r["_ident"]: h for r in recs for h in [hints_for(r, self.mode)] if h}
        return self.client.read_batch(
            images, self.model, self.rules, self.notes,
            should_stop=self.should_stop, on_wait=on_wait, hints=hints or None,
            on_throttle=lambda d: self.emit("throttle", {"delay": d}))

    def _cache_key(self, rec: dict) -> str:
        # odczyt z danymi z nazwy pliku to inne pytanie niz odczyt "na slepo",
        # a inna strona PDF-a to inny obraz
        key = self.model + ("|nazwa" if hints_for(rec, self.mode) else "")
        if self.mode == assess.MODE_PAGES:
            key += "|strony"
        if self.page and rec.get("kind") == "pdf":
            key += f"|s{self.page + 1}"
        return key

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
                    _apply(rec, hit, self.model, json.dumps(hit, ensure_ascii=False, indent=1), self.mode)
                    rec["from_cache"] = True
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
            except DailyLimitReached as exc:
                self.daily_limit = True
                self.emit("daily_limit", {"error": str(exc)})
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
                        _apply(rec, one[0], self.model, self._store(rec, one[0]), self.mode)
                        rec["from_cache"] = False
                    except FatalApiError as exc2:
                        self.emit("fatal", {"error": str(exc2)})
                        self._stop_evt.set()
                        break
                    except DailyLimitReached as exc2:
                        self.daily_limit = True
                        self.emit("daily_limit", {"error": str(exc2)})
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
                _apply(rec, item, self.model, self._store(rec, item), self.mode)
                rec["from_cache"] = False
                done_files += 1
                self.emit("record", {"rec": rec})

            self.emit("progress", {"done": done_files, "total": total,
                                   "elapsed": self.elapsed()})

        self.emit("finished", {"stopped": self._stop_evt.is_set() or self.daily_limit,
                               "daily_limit": self.daily_limit,
                               "done": done_files, "total": total,
                               "from_cache": from_cache,
                               "elapsed": self.elapsed()})

    def elapsed(self) -> float:
        if not self.started_at:
            return 0.0
        return max(0.0, time.time() - self.started_at - self.paused_total)

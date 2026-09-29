"""Watek roboczy: renderowanie, wysylka batchy, cache, pauza/stop, postep."""
from __future__ import annotations

import threading
import time

import cache_db
import render
from config import BATCH_SIZE
from openrouter_client import FatalApiError, OpenRouterClient

FIELDS = ("title", "language", "date_iso", "date_raw", "month_raw", "year_printed",
          "issue_number", "issue_suffix", "page_number", "is_cover", "confidence")


def _apply(rec: dict, item: dict, model: str, raw: str) -> None:
    rec["title"] = item.get("publication_title")
    rec["language"] = item.get("language")
    rec["date_iso"] = item.get("date_iso")
    rec["date_raw"] = item.get("date_raw")
    rec["month_raw"] = item.get("month_raw")
    rec["year_printed"] = item.get("year_printed")
    rec["issue_number"] = item.get("issue_number")
    rec["issue_suffix"] = item.get("issue_suffix")
    rec["page_number"] = item.get("page_number")
    rec["is_cover"] = item.get("is_cover")
    try:
        rec["confidence"] = round(float(item.get("confidence")), 2)
    except Exception:
        rec["confidence"] = None
    rec["model"] = model
    rec["raw"] = raw

    missing = []
    if not rec.get("date_iso"):
        missing.append("data")
    if not rec.get("issue_number"):
        missing.append("nr wydania")
    if rec.get("kind") == "image" and not rec.get("page_number"):
        missing.append("nr strony")
    rec["status"] = "brak danych" if missing else "odczytano"
    rec["note"] = ("nie odczytano: " + ", ".join(missing)) if missing else ""


class ReadWorker(threading.Thread):
    """Przetwarza liste rekordow. Komunikuje sie z GUI przez kolejke zdarzen."""

    def __init__(self, records: list[dict], api_key: str, model: str, queue,
                 batch_size: int = BATCH_SIZE, use_cache: bool = True, rpm: int = 20):
        super().__init__(daemon=True)
        self.records = records
        self.model = model
        self.queue = queue
        self.batch_size = max(1, int(batch_size))
        self.use_cache = use_cache
        self.client = OpenRouterClient(api_key, model, rpm=rpm)

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
                    hit = cache_db.get(h, self.model)
                except Exception:
                    hit = None
                if hit:
                    _apply(rec, hit, self.model, hit.get("_raw", ""))
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
            urls, ok_batch = [], []
            for rec in batch:
                if self._stop_evt.is_set():
                    break
                try:
                    urls.append(render.to_data_url(rec["path"]))
                    ok_batch.append(rec)
                except Exception as exc:
                    rec["status"] = "blad odczytu pliku"
                    rec["note"] = str(exc)
                    done_files += 1
                    self.emit("record", {"rec": rec})
            if not ok_batch or self._stop_evt.is_set():
                continue

            try:
                items, raw = self.client.read_batch(
                    urls, self.model,
                    should_stop=self.should_stop,
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
                        one, raw1 = self.client.read_batch(
                            [render.to_data_url(rec["path"])], self.model,
                            should_stop=self.should_stop)
                        _apply(rec, one[0], self.model, raw1)
                        if self.use_cache and rec.get("_hash"):
                            payload = {k: one[0].get(k) for k in one[0]}
                            payload["_raw"] = raw1
                            cache_db.put(rec["_hash"], self.model, payload)
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
                _apply(rec, item, self.model, raw)
                if self.use_cache and rec.get("_hash"):
                    payload = dict(item)
                    payload["_raw"] = raw
                    try:
                        cache_db.put(rec["_hash"], self.model, payload)
                    except Exception:
                        pass
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

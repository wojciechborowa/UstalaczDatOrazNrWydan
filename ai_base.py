"""Czesc wspolna klientow AI: limit zapytan, ponawianie, bledy."""
from __future__ import annotations

import threading
import time
from collections import deque

import requests

from config import FREE_RPM, MAX_RETRIES


class RateLimitError(RuntimeError):
    """429 - limit zapytan. Da sie ponowic."""


class FatalApiError(RuntimeError):
    """Zly klucz, brak srodkow, zly model - nie ma sensu ponawiac."""


class RateLimiter:
    """Okno przesuwne: max `rpm` zapytan na 60 sekund."""

    def __init__(self, rpm: int = FREE_RPM):
        self.rpm = max(1, rpm)
        self._times: deque[float] = deque()
        self._lock = threading.Lock()

    def wait(self, should_stop=None) -> None:
        while True:
            with self._lock:
                now = time.time()
                while self._times and now - self._times[0] >= 60.0:
                    self._times.popleft()
                if len(self._times) < self.rpm:
                    self._times.append(now)
                    return
                sleep_for = 60.0 - (now - self._times[0]) + 0.05
            end = time.time() + sleep_for
            while time.time() < end:
                if should_stop and should_stop():
                    return
                time.sleep(0.2)


class BaseClient:
    """Wspolny szkielet: klient dostawcy implementuje tylko _post() i _extract()."""

    provider = ""

    def __init__(self, api_key: str, model: str = "", rpm: int = FREE_RPM):
        self.api_key = (api_key or "").strip()
        self.model = model
        self.limiter = RateLimiter(rpm)
        self.session = requests.Session()

    # --- do nadpisania ----------------------------------------------------
    def _post(self, model: str, images: list[tuple[str, str]], prompt: str) -> requests.Response:
        raise NotImplementedError

    def _extract(self, body: dict) -> str:
        """Tekst odpowiedzi modelu z ciala odpowiedzi HTTP."""
        raise NotImplementedError

    def _fatal_for(self, r: requests.Response) -> str | None:
        """Komunikat, jesli odpowiedz oznacza blad, ktorego nie warto ponawiac."""
        if r.status_code in (401, 403):
            return f"Odmowa dostepu ({r.status_code}): {r.text[:300]}"
        return None

    def _retry_after(self, r: requests.Response) -> float | None:
        try:
            return float(r.headers.get("Retry-After", ""))
        except ValueError:
            return None

    # --- wspolne ------------------------------------------------------------
    def read_batch(self, images: list[tuple[str, str]], model: str | None = None,
                   rules: str | None = None, notes: str | None = None,
                   should_stop=None, on_wait=None) -> tuple[list[dict], str]:
        """Wysyla obrazy (identyfikator, JPEG w base64) w jednym zapytaniu.

        Zwraca (wyniki w kolejnosci `images`, surowa odpowiedz)."""
        from prompt import build_user_prompt, parse_response

        if not self.api_key:
            raise FatalApiError("Nie podano klucza API.")
        model = model or self.model
        if not model:
            raise FatalApiError("Nie wybrano modelu.")
        ids = [i for i, _ in images]
        prompt = build_user_prompt(ids, rules, notes)

        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            if should_stop and should_stop():
                raise RuntimeError("przerwano")
            self.limiter.wait(should_stop)
            if should_stop and should_stop():
                raise RuntimeError("przerwano")

            try:
                r = self._post(model, images, prompt)
            except requests.RequestException as exc:
                last_err = exc
                self._backoff(attempt, None, on_wait, should_stop)
                continue

            if r.status_code == 200:
                try:
                    text = self._extract(r.json())
                    return parse_response(text, ids), text
                except Exception as exc:
                    # zla struktura odpowiedzi - model bywa niedeterministyczny, ponawiamy
                    last_err = exc
                    if attempt == MAX_RETRIES:
                        raise
                    self._backoff(attempt, None, on_wait, should_stop)
                    continue

            fatal = self._fatal_for(r)
            if fatal:
                raise FatalApiError(fatal)
            if r.status_code == 429:
                last_err = RateLimitError(r.text[:300])
                if attempt == MAX_RETRIES:
                    raise RateLimitError(
                        "429 - wyczerpany limit zapytan (na minute lub dzienny). "
                        "Sprobuj pozniej, zmniejsz limit zapytan/min albo zmien model.")
                self._backoff(attempt, self._retry_after(r), on_wait, should_stop)
                continue

            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            if attempt == MAX_RETRIES:
                raise last_err
            self._backoff(attempt, None, on_wait, should_stop)

        raise last_err or RuntimeError("nieznany blad zapytania")

    @staticmethod
    def _backoff(attempt: int, retry_after: float | None, on_wait, should_stop) -> None:
        delay = 2.0 ** attempt
        if retry_after:
            delay = max(delay, retry_after)
        delay = min(delay, 120.0)
        if on_wait:
            on_wait(delay, attempt)
        end = time.time() + delay
        while time.time() < end:
            if should_stop and should_stop():
                return
            time.sleep(0.2)

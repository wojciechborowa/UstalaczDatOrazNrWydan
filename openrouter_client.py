"""Klient OpenRouter: lista modeli, wysylka batcha obrazow, limity i ponawianie."""
from __future__ import annotations

import threading
import time
from collections import deque

import requests

from config import (FREE_RPM, MAX_RETRIES, OPENROUTER_BASE, REFERER,
                    REQUEST_TIMEOUT, X_TITLE)
from prompt import SYSTEM_PROMPT, build_user_prompt, parse_response


class RateLimitError(RuntimeError):
    """429 - limit zapytan. Da sie ponowic."""


class FatalApiError(RuntimeError):
    """402/401 - nie ma sensu ponawiac."""


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


class OpenRouterClient:
    def __init__(self, api_key: str, model: str = "", rpm: int = FREE_RPM):
        self.api_key = (api_key or "").strip()
        self.model = model
        self.limiter = RateLimiter(rpm)
        self.session = requests.Session()

    # ---------------------------------------------------------------- helpers
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": REFERER,
            "X-Title": X_TITLE,
        }

    # ------------------------------------------------------------- key & models
    def key_info(self) -> dict:
        r = self.session.get(f"{OPENROUTER_BASE}/key", headers=self._headers(),
                             timeout=30)
        if r.status_code == 401:
            raise FatalApiError("Klucz API odrzucony (401). Sprawdz, czy jest poprawny.")
        r.raise_for_status()
        return r.json().get("data", {})

    def list_models(self) -> list[dict]:
        r = self.session.get(f"{OPENROUTER_BASE}/models", timeout=60)
        r.raise_for_status()
        return r.json().get("data", [])

    @staticmethod
    def is_free(m: dict) -> bool:
        if str(m.get("id", "")).endswith(":free"):
            return True
        pr = m.get("pricing") or {}
        try:
            return float(pr.get("prompt", 1)) == 0.0 and float(pr.get("completion", 1)) == 0.0
        except Exception:
            return False

    @staticmethod
    def supports_images(m: dict) -> bool:
        arch = m.get("architecture") or {}
        mods = arch.get("input_modalities") or []
        if isinstance(mods, list) and any("image" in str(x).lower() for x in mods):
            return True
        # starsze wpisy API opisuja to inaczej
        return "image" in str(arch.get("modality", "")).lower()

    def vision_models(self, only_free: bool = True) -> list[dict]:
        out = []
        for m in self.list_models():
            if not self.supports_images(m):
                continue
            if only_free and not self.is_free(m):
                continue
            out.append(m)
        out.sort(key=lambda m: str(m.get("id", "")))
        return out

    # --------------------------------------------------------------- inference
    def read_batch(self, data_urls: list[str], model: str | None = None,
                   should_stop=None, on_wait=None) -> tuple[list[dict], str]:
        """Wysyla N obrazow w jednym zapytaniu. Zwraca (rekordy, surowa_odpowiedz)."""
        if not self.api_key:
            raise FatalApiError("Nie podano klucza API.")
        model = model or self.model
        if not model:
            raise FatalApiError("Nie wybrano modelu.")

        n = len(data_urls)
        content: list[dict] = [{"type": "text", "text": build_user_prompt(n)}]
        for url in data_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})

        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }

        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            if should_stop and should_stop():
                raise RuntimeError("przerwano")

            self.limiter.wait(should_stop)
            if should_stop and should_stop():
                raise RuntimeError("przerwano")

            try:
                r = self.session.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers=self._headers(), json=payload, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                last_err = exc
                self._backoff(attempt, None, on_wait, should_stop)
                continue

            if r.status_code == 200:
                try:
                    body = r.json()
                    text = body["choices"][0]["message"]["content"]
                    if isinstance(text, list):  # niektore modele zwracaja liste blokow
                        text = "".join(part.get("text", "") for part in text
                                       if isinstance(part, dict))
                    return parse_response(text, n), text
                except ValueError as exc:
                    # zla struktura odpowiedzi - warto ponowic, model bywa niedeterministyczny
                    last_err = exc
                    if attempt == MAX_RETRIES:
                        raise
                    self._backoff(attempt, None, on_wait, should_stop)
                    continue
                except Exception as exc:
                    last_err = exc
                    self._backoff(attempt, None, on_wait, should_stop)
                    continue

            if r.status_code in (401, 403):
                raise FatalApiError(f"Odmowa dostepu ({r.status_code}): {r.text[:300]}")
            if r.status_code == 402:
                raise FatalApiError(
                    "402 - brak srodkow lub przekroczony limit kredytow na kluczu. "
                    "Doladuj konto na openrouter.ai/settings/credits.")
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                last_err = RateLimitError(r.text[:300])
                if attempt == MAX_RETRIES:
                    raise RateLimitError(
                        "429 - wyczerpany limit zapytan (na minute lub dzienny). "
                        "Sprobuj pozniej albo doladuj konto, zeby podniesc limit dzienny.")
                self._backoff(attempt, retry_after, on_wait, should_stop)
                continue

            last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            if attempt == MAX_RETRIES:
                raise last_err
            self._backoff(attempt, None, on_wait, should_stop)

        raise last_err or RuntimeError("nieznany blad zapytania")

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None, on_wait, should_stop) -> None:
        delay = 2.0 ** attempt
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        delay = min(delay, 120.0)
        if on_wait:
            on_wait(delay, attempt)
        end = time.time() + delay
        while time.time() < end:
            if should_stop and should_stop():
                return
            time.sleep(0.2)

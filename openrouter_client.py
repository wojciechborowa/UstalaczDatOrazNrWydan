"""Klient OpenRouter: lista modeli, wysylka batcha obrazow, limity i ponawianie."""
from __future__ import annotations

import requests

from ai_base import BaseClient, FatalApiError, RateLimiter, RateLimitError  # noqa: F401
from config import OPENROUTER_BASE, REFERER, REQUEST_TIMEOUT, X_TITLE
from prompt import SYSTEM_PROMPT


class OpenRouterClient(BaseClient):
    provider = "openrouter"

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
    def _post(self, model: str, images: list[tuple[str, str]], prompt: str) -> requests.Response:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for _ident, b64 in images:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
        }
        return self.session.post(f"{OPENROUTER_BASE}/chat/completions",
                                 headers=self._headers(), json=payload,
                                 timeout=REQUEST_TIMEOUT)

    def _extract(self, body: dict) -> str:
        text = body["choices"][0]["message"]["content"]
        if isinstance(text, list):  # niektore modele zwracaja liste blokow
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
        return text

    def _tokens(self, body: dict) -> int | None:
        return (body.get("usage") or {}).get("total_tokens")

    def _fatal_for(self, r: requests.Response) -> str | None:
        if r.status_code == 402:
            return ("402 - brak srodkow lub przekroczony limit kredytow na kluczu. "
                    "Doladuj konto na openrouter.ai/settings/credits.")
        return super()._fatal_for(r)

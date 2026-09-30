"""Klient Google Gemini API (AI Studio): lista modeli, wysylka batcha obrazow.

Klucz idzie w naglowku x-goog-api-key, a nie w adresie zapytania - adres z kluczem
latwo trafia do logow i komunikatow bledow.
"""
from __future__ import annotations

import json
import re

import requests

from ai_base import BaseClient, FatalApiError
from config import GEMINI_BASE, REQUEST_TIMEOUT
from prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT

# Gdy nie da sie pobrac listy modeli - lista, od ktorej mozna zaczac.
FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-2.5-flash",
]

# modele, ktore nie czytaja obrazow albo sluza do czegos innego
_SKIP = re.compile(r"embedding|aqa|tts|audio|image-generation|imagen|veo|live|robotics|"
                   r"computer-use|native-audio|learnlm|gemma", re.I)


def model_rank(name: str) -> tuple:
    """Kolejnosc na liscie: najpierw tanie flash-lite, potem flash, potem reszta."""
    n = name.lower()
    tier = 0 if "flash-lite" in n else 1 if "flash" in n else 2
    return (tier, "preview" in n or "exp" in n, n)


class GeminiClient(BaseClient):
    provider = "gemini"

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    # ------------------------------------------------------------- key & models
    def list_models(self) -> list[dict]:
        if not self.api_key:
            raise FatalApiError("Nie podano klucza API.")
        out, token = [], ""
        for _ in range(10):
            params = {"pageSize": 200}
            if token:
                params["pageToken"] = token
            r = self.session.get(f"{GEMINI_BASE}/models", headers=self._headers(),
                                 params=params, timeout=60)
            msg = self._fatal_for(r)
            if msg:
                raise FatalApiError(msg)
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("models", []))
            token = body.get("nextPageToken", "")
            if not token:
                break
        return out

    def vision_models(self) -> list[dict]:
        models = []
        for m in self.list_models():
            methods = m.get("supportedGenerationMethods") or []
            name = str(m.get("name", "")).replace("models/", "")
            if "generateContent" not in methods or not name.startswith("gemini"):
                continue
            if _SKIP.search(name):
                continue
            m["id"] = name
            models.append(m)
        models.sort(key=lambda m: model_rank(m["id"]))
        return models

    def key_info(self) -> dict:
        """Test klucza - Gemini nie podaje limitow przez API, wiec sprawdzamy dostep."""
        models = self.vision_models()
        return {"models": len(models)}

    # --------------------------------------------------------------- inference
    def _post(self, model: str, images: list[tuple[str, str]], prompt: str) -> requests.Response:
        parts: list[dict] = []
        for ident, b64 in images:
            parts.append({"text": f"Image with identifier {ident}:"})
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
        parts.append({"text": prompt})
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": RESPONSE_SCHEMA,
            },
        }
        model = model.replace("models/", "")
        return self.session.post(f"{GEMINI_BASE}/models/{model}:generateContent",
                                 headers=self._headers(), json=payload,
                                 timeout=REQUEST_TIMEOUT)

    def _extract(self, body: dict) -> str:
        cands = body.get("candidates") or []
        if not cands:
            reason = (body.get("promptFeedback") or {}).get("blockReason")
            raise ValueError(f"pusta odpowiedz modelu{f' (zablokowano: {reason})' if reason else ''}")
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        if not text:
            raise ValueError(f"odpowiedz bez tresci (powod: {cands[0].get('finishReason')})")
        return text

    def _tokens(self, body: dict) -> int | None:
        meta = body.get("usageMetadata") or {}
        return meta.get("totalTokenCount") or meta.get("promptTokenCount")

    # ------------------------------------------------------------------ bledy
    @staticmethod
    def _error(r: requests.Response) -> dict:
        try:
            return (r.json() or {}).get("error") or {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def _fatal_for(self, r: requests.Response) -> str | None:
        if r.status_code in (200,):
            return None
        err = self._error(r)
        msg = str(err.get("message") or r.text[:300])
        details = json.dumps(err.get("details") or [])
        if r.status_code == 400 and ("API_KEY_INVALID" in details or "API key not valid" in msg):
            return "Klucz Gemini odrzucony. Sprawdz, czy jest poprawny (aistudio.google.com/apikey)."
        if r.status_code in (401, 403):
            return f"Odmowa dostepu ({r.status_code}): {msg}"
        if r.status_code == 404:
            return f"Model nie istnieje albo nie jest dostepny dla tego klucza: {msg}"
        if r.status_code == 429 and re.search(r"PerDay|per day", details + msg, re.I):
            return ("429 - wyczerpany DZIENNY limit darmowych zapytan dla tego modelu. "
                    "Limit odnawia sie o polnocy czasu pacyficznego; mozesz tez wybrac inny model.")
        return None

    def _retry_after(self, r: requests.Response) -> float | None:
        for d in self._error(r).get("details") or []:
            delay = str(d.get("retryDelay") or "")
            m = re.match(r"([\d.]+)s$", delay)
            if m:
                return float(m.group(1)) + 1.0
        return super()._retry_after(r)

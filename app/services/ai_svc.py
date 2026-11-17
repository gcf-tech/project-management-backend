"""Cliente mínimo para la API de OpenAI (Chat Completions) usado por el módulo de
planificación asistida del Deck.

Diseño (ver docs/adr/adr-006-ai-planning-module.md):
- La llamada al LLM vive SOLO en el backend: la clave nunca llega al navegador.
- Una única función `complete_json()` que devuelve un dict ya parseado. El
  proveedor se elige por `config.AI_PROVIDER`; hoy solo OpenAI, pero la firma es
  agnóstica para poder añadir Gemini sin tocar a los llamadores.
- Best-effort con timeout y reintento ante 429/5xx, mismo patrón que
  `translate_svc.py`.
"""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.core import config

_OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
_OPENAI_EMBED_ENDPOINT = "https://api.openai.com/v1/embeddings"
EMBED_MODEL = "text-embedding-3-small"


class AIError(Exception):
    """Error controlado del servicio de IA (deshabilitado, red, JSON inválido)."""


@dataclass
class AIResult:
    data: Dict[str, Any]
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_text: str = ""


def extract_json(text: str) -> Dict[str, Any]:
    """Extrae el primer objeto JSON balanceado de un texto. Replica el
    `extractJSON` de AIASE: tolera texto o fences ```json antes/después."""
    if not text:
        raise AIError("Respuesta vacía del modelo")
    text = text.strip()
    # Quitar fences de markdown si vienen.
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        raise AIError("El modelo no devolvió un objeto JSON")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise AIError(f"JSON inválido del modelo: {e}") from e
    raise AIError("JSON incompleto en la respuesta del modelo")


async def _openai_chat(system_prompt: str, user_prompt: str, *,
                       temperature: float, max_tokens: int) -> AIResult:
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": config.AI_MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=config.AI_TIMEOUT_S) as client:
                r = await client.post(_OPENAI_ENDPOINT, headers=headers, json=body)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code != 200:
                detail = r.text[:300]
                try:
                    detail = r.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                raise AIError(f"OpenAI {r.status_code}: {detail}")
            payload = r.json()
            text = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage", {}) or {}
            return AIResult(
                data=extract_json(text),
                model=payload.get("model", config.AI_MODEL),
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                raw_text=text,
            )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = e
            if attempt < 2:
                await asyncio.sleep(2 * (attempt + 1))
                continue
            raise AIError(f"Sin respuesta de OpenAI: {e}") from e
    raise AIError(f"Fallo en la llamada a OpenAI: {last_err}")


async def complete_json(system_prompt: str, user_prompt: str, *,
                        temperature: Optional[float] = None,
                        max_tokens: int = 4096) -> AIResult:
    """Pide al modelo una respuesta JSON y la devuelve parseada.
    Lanza `AIError` si el servicio está deshabilitado o la llamada falla."""
    if not config.AI_ENABLED:
        raise AIError("El servicio de IA no está configurado (OPENAI_API_KEY vacía)")
    temp = config.AI_TEMPERATURE if temperature is None else temperature
    if config.AI_PROVIDER == "openai":
        return await _openai_chat(system_prompt, user_prompt, temperature=temp, max_tokens=max_tokens)
    raise AIError(f"Proveedor de IA no soportado: {config.AI_PROVIDER}")


async def embed(texts: List[str]) -> List[List[float]]:
    """Embeddings de una lista de textos (OpenAI). Usado por el backtest para
    comparar títulos de subtareas por similitud semántica, no por palabras exactas."""
    if not config.AI_ENABLED:
        raise AIError("El servicio de IA no está configurado (OPENAI_API_KEY vacía)")
    if not texts:
        return []
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"}
    body = {"model": EMBED_MODEL, "input": [t[:2000] for t in texts]}
    async with httpx.AsyncClient(timeout=config.AI_TIMEOUT_S) as client:
        r = await client.post(_OPENAI_EMBED_ENDPOINT, headers=headers, json=body)
    if r.status_code != 200:
        raise AIError(f"OpenAI embeddings {r.status_code}: {r.text[:200]}")
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]

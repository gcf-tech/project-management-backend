"""Traducción vía Google Cloud Translation API v2 (con API key). Best-effort +
caché en memoria para no re-traducir lo mismo. Usado por el botón 'Traducir' de
los comentarios."""
import hashlib
from collections import OrderedDict
from typing import Optional

import httpx

from app.core import config

_ENDPOINT = "https://translation.googleapis.com/language/translate/v2"
_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_CACHE_MAX = 2000


def _key(text: str, target: str, fmt: str) -> str:
    h = hashlib.sha1(f"{target}|{fmt}|{text}".encode("utf-8")).hexdigest()
    return h


async def translate(text: str, target: str, fmt: str = "text") -> Optional[dict]:
    """Devuelve {'text': ..., 'detected': ...} o None si está deshabilitado/falla.
    `fmt` = 'html' para conservar el formato, 'text' para texto plano."""
    if not config.TRANSLATE_ENABLED or not (text and text.strip()):
        return None
    target = (target or "es").split("-")[0].lower()
    fmt = "html" if fmt == "html" else "text"

    ck = _key(text, target, fmt)
    cached = _CACHE.get(ck)
    if cached is not None:
        _CACHE.move_to_end(ck)
        return cached

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                _ENDPOINT,
                params={"key": config.GOOGLE_TRANSLATE_API_KEY},
                json={"q": text, "target": target, "format": fmt},
            )
            r.raise_for_status()
            tr = r.json()["data"]["translations"][0]
            out = {"text": tr.get("translatedText", ""),
                   "detected": (tr.get("detectedSourceLanguage") or "").lower()}
    except Exception as e:  # best-effort
        print(f"[translate] fallo: {e}")
        return None

    _CACHE[ck] = out
    _CACHE.move_to_end(ck)
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return out

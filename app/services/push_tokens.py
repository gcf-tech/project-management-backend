"""Cacheo CIFRADO del access token de Nextcloud, para el push de Talk.

Guardamos solo el access token (corto, ~1h), nunca el refresh token: el cliente
rota el refresh en cada uso, y un segundo refrescador rompería su sesión. Con el
access token cacheado, el poller de Talk consulta en nombre del usuario mientras
el token siga vivo (ventana ~1h desde la última actividad).
"""
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken

from app.core import config
from app.db.models import WorkspaceUserToken
from app.core.datetime_utils import utc_now

_fernet = None


def cifrado_disponible() -> bool:
    return bool(config.PUSH_TOKEN_KEY)


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(config.PUSH_TOKEN_KEY.encode())
    return _fernet


def guardar_access_token(db, user_id: int, access_token: str, expires_in: int = 3600) -> None:
    """Upsert del access token cifrado del usuario. Silencioso si no hay clave."""
    if not cifrado_disponible() or not access_token:
        return
    enc = _f().encrypt(access_token.encode()).decode()
    # margen de 60s para no usar un token a punto de expirar
    exp = utc_now() + timedelta(seconds=max(60, int(expires_in) - 60))
    row = db.query(WorkspaceUserToken).filter(WorkspaceUserToken.user_id == user_id).first()
    if row:
        row.access_token_enc = enc
        row.expires_at = exp
        row.updated_at = utc_now()
    else:
        db.add(WorkspaceUserToken(user_id=user_id, access_token_enc=enc, expires_at=exp,
                                  talk_seen=None, updated_at=utc_now()))
    db.commit()


def leer_access_token(row: WorkspaceUserToken):
    """Descifra el token de una fila. None si no se puede."""
    if not cifrado_disponible() or not row or not row.access_token_enc:
        return None
    try:
        return _f().decrypt(row.access_token_enc.encode()).decode()
    except (InvalidToken, Exception):
        return None

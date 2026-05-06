"""AES-256-GCM helpers for secrets stored at rest.

Used exclusively for CalDAV App Passwords (users.nc_caldav_token_* columns).
The master key must be present in CALDAV_ENCRYPTION_KEY when
CALDAV_AUTH_MODE=app_password — config.py validates this at startup.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import CALDAV_ENCRYPTION_KEY_BYTES

_NONCE_LEN = 12  # AES-GCM standard: 96-bit nonce


def encrypt_secret(plaintext: str) -> tuple[bytes, bytes]:
    """Encrypt *plaintext* → ``(ciphertext_with_tag, nonce)``.

    The 16-byte GCM authentication tag is appended to *ciphertext* by AESGCM.
    Store ciphertext in ``nc_caldav_token_ciphertext`` and nonce in
    ``nc_caldav_token_iv``.
    """
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(CALDAV_ENCRYPTION_KEY_BYTES).encrypt(nonce, plaintext.encode("utf-8"), None)
    return ciphertext, nonce


def decrypt_secret(ciphertext: bytes, nonce: bytes) -> str:
    """Decrypt and authenticate → plaintext string.

    Raises ``cryptography.exceptions.InvalidTag`` if the ciphertext was
    tampered with or the key is wrong.
    """
    return AESGCM(CALDAV_ENCRYPTION_KEY_BYTES).decrypt(nonce, ciphertext, None).decode("utf-8")

"""Credential encryption helpers for at-rest storage in DB."""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


ENCRYPTION_PREFIX = "enc:v1:"


def _derive_key(secret: str) -> bytes:
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    configured = getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if configured:
        key = configured.encode("utf-8")
    else:
        key = _derive_key(settings.SECRET_KEY)
    return Fernet(key)


def is_encrypted(value: str) -> bool:
    return bool(value) and value.startswith(ENCRYPTION_PREFIX)


def encrypt_if_needed(value: str) -> str:
    if not value or is_encrypted(value):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{ENCRYPTION_PREFIX}{token}"


def decrypt_if_needed(value: str) -> str:
    if not value:
        return ""
    if not is_encrypted(value):
        return value
    token = value[len(ENCRYPTION_PREFIX) :]
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""

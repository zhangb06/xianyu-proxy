from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("utf-8")).digest()


def encrypt(plaintext: str, secret: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key(secret)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt(token: str, secret: str) -> str:
    raw = base64.b64decode(token)
    return AESGCM(_key(secret)).decrypt(raw[:12], raw[12:], None).decode("utf-8")

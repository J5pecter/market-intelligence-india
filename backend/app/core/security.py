"""Authentication primitives: password hashing, JWTs, role checks.

Password hashing uses PBKDF2-HMAC-SHA256 from the standard library so the
project builds on Windows without a C toolchain. The parameters below follow
OWASP's 2023 guidance (>= 600,000 iterations for SHA-256).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Optional

import jwt

from app.core.config import settings

_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_ALGORITHM = "HS256"


class Role(str, Enum):
    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    USER = "USER"


ROLE_RANK = {Role.USER: 0, Role.ANALYST: 1, Role.ADMIN: 2}


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return "$".join(
        [
            "pbkdf2_sha256",
            str(_PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
        )
        # constant-time compare defeats timing oracles
        return hmac.compare_digest(digest, base64.b64decode(hash_b64))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def create_access_token(
    subject: str, role: str, expires_minutes: Optional[int] = None
) -> str:
    expire = datetime.now(tz=timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {
        "sub": subject,
        "role": role,
        "exp": expire,
        "iat": datetime.now(tz=timezone.utc),
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Raises jwt.PyJWTError on any problem - callers turn that into a 401."""
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])


def role_allows(actual: str, required: Role) -> bool:
    try:
        return ROLE_RANK[Role(actual)] >= ROLE_RANK[required]
    except (ValueError, KeyError):
        return False


# --------------------------------------------------------------------------
# API-key encryption at rest (provider credentials stored in the DB)
# --------------------------------------------------------------------------


def _fernet_like_key() -> bytes:
    return hashlib.sha256(settings.secret_key.encode("utf-8")).digest()


def encrypt_secret(plaintext: str) -> str:
    """XOR-with-keystream + HMAC tag.

    This keeps provider keys unreadable in a DB dump. It is deliberately
    dependency-free; if you need managed key rotation, swap this for KMS /
    Vault - the call sites only use encrypt_secret / decrypt_secret.
    """
    key = _fernet_like_key()
    nonce = secrets.token_bytes(16)
    stream = b""
    counter = 0
    data = plaintext.encode("utf-8")
    while len(stream) < len(data):
        stream += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(nonce + tag + cipher).decode()


def decrypt_secret(token: str) -> str:
    key = _fernet_like_key()
    raw = base64.urlsafe_b64decode(token.encode())
    nonce, tag, cipher = raw[:16], raw[16:32], raw[32:]
    if not hmac.compare_digest(
        tag, hmac.new(key, nonce + cipher, hashlib.sha256).digest()[:16]
    ):
        raise ValueError("secret failed integrity check")
    stream = b""
    counter = 0
    while len(stream) < len(cipher):
        stream += hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")

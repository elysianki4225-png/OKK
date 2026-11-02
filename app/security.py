"""安全模块：API 密钥加密存储、管理员口令哈希、会话令牌。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from cryptography.fernet import Fernet

from . import config

PBKDF2_ITER = 200_000


# ---------------- Fernet（交易所 / LLM 密钥落盘加密） ----------------
def _fernet() -> Fernet:
    raw = hashlib.sha256(config.master_key()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:
        return ""


# ---------------- 管理员口令 ----------------
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$", 1)
        salt = base64.b64decode(salt_b64)
        dk = base64.b64decode(dk_b64)
        cand = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITER)
        return hmac.compare_digest(cand, dk)
    except Exception:
        return False


# ---------------- 会话令牌（HMAC 签名，免依赖） ----------------
def _sign(payload: str) -> str:
    return hmac.new(config.secret_key(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def issue_token(ttl: int = 7 * 86400) -> str:
    body = f"{int(time.time()) + ttl}.{secrets.token_hex(8)}"
    return f"{body}.{_sign(body)}"


def verify_token(token: str) -> bool:
    try:
        body, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(body)):
            return False
        exp = int(body.split(".", 1)[0])
        return exp > time.time()
    except Exception:
        return False


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]


def json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)

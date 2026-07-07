"""Encrypt/decrypt the auth cookies at rest with a Fernet master key.

Key source (first wins):
  1. XBOT_MASTER_KEY env var (a Fernet key — generate with: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())")
  2. data/master.key (auto-generated, chmod 600)

The on-disk key file lets the backend auto-start unattended (required for the
scheduler). If you want a password-derived key instead, that needs an interactive
unlock step — see the "Explicitly skipped" section of the plan.
"""
import os

from cryptography.fernet import Fernet

# ponytail: anchor to repo root — see server/db.py. Keeps the master key stable
# across restarts/cwds so encrypted cookies always decrypt.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KEY_FILE = os.path.join(_ROOT, "data", "master.key")
_fernet = None


def _load_or_create_key() -> bytes:
    env = os.environ.get("XBOT_MASTER_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env
    os.makedirs(os.path.join(_ROOT, "data"), exist_ok=True)
    if os.path.exists(_KEY_FILE):
        with open(_KEY_FILE, "rb") as f:
            return f.read()
    key = Fernet.generate_key()
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def encrypt(plaintext: str) -> bytes:
    if not plaintext:
        return b""
    return _f().encrypt(plaintext.encode())


def decrypt(token) -> str:
    if not token:
        return ""
    if isinstance(token, str):
        token = token.encode()
    return _f().decrypt(token).decode()

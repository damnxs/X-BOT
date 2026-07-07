"""App-level password auth: a single shared password (env XBOT_UI_PASSWORD)
protects every /api endpoint except /api/login. Login sets an HMAC-signed
session cookie.

Auth is ON by default (fail-closed). To turn it off for local dev, set
XBOT_AUTH_DISABLED=1. Otherwise every /api endpoint (and the UI) requires a
valid session — and if XBOT_UI_PASSWORD is not set, login is impossible until
you set it. On the VPS the deploy script writes both secrets to /etc/xbot.env.
"""
import base64
import hashlib
import hmac
import json
import os
import time


def password_configured():
    return bool(os.environ.get("XBOT_UI_PASSWORD"))


def auth_enabled():
    """Auth is on by default. Explicit opt-out for local dev only
    (XBOT_AUTH_DISABLED=1). Default-on keeps a freshly cloned/running app
    locked instead of silently wide open."""
    return os.environ.get("XBOT_AUTH_DISABLED", "").lower() not in ("1", "true", "yes")


def check_password(pw):
    cfg = os.environ.get("XBOT_UI_PASSWORD", "")
    return bool(cfg) and hmac.compare_digest(pw, cfg)


def _secret():
    return os.environ.get("XBOT_SESSION_SECRET") or "dev-insecure-change-me"


def _sign(payload):
    return hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(days=7):
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + days * 86400}).encode()
    ).decode()
    return f"{payload}.{_sign(payload)}"


def verify_session(token):
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data.get("exp", 0)) > time.time()
    except Exception:
        return False


if __name__ == "__main__":
    # ponytail: self-check — fail fast if session signing or the auth default breaks.
    os.environ.pop("XBOT_UI_PASSWORD", None)
    os.environ.pop("XBOT_AUTH_DISABLED", None)
    assert auth_enabled() is True                      # fail-closed by default
    assert password_configured() is False
    tok = make_session()
    assert verify_session(tok) and not verify_session(tok + "x")  # valid + tamper
    os.environ["XBOT_AUTH_DISABLED"] = "1"
    assert auth_enabled() is False                     # explicit dev opt-in
    print("auth self-check ok")

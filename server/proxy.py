"""Proxy diagnostic via requests (the same client the provider's docs prescribe).

Probes the exit IP over HTTP then HTTPS THROUGH the proxy, with basic auth. Why
requests and not a headless browser: an authenticated HTTP proxy connects cleanly
with requests (it sends Proxy-Authorization on the CONNECT), where chromium can
stall on the CONNECT handshake and report "Malformed reply" for minutes. The bot
itself still runs the proxy on its own browser context; this is only the health
probe, and it answers the one question that matters: does this proxy connect and
what's the exit IP.

Run as a sync call inside a FastAPI `def` route (threadpool) so the blocking
network work can't stall the event loop.
"""
import logging
import re
import time

import requests

_log = logging.getLogger("server.proxy")

# ipify answers the exit IP as plain text over both http and https.
_DIAG_TARGETS = (
    ("http://api.ipify.org", "HTTP"),
    ("https://api.ipify.org", "HTTPS"),
)

# strip any user:pass@ from a proxy URL echoed back in an error message
_CREDS_RE = re.compile(r"(://)[^@/]+@")


def _scrub(s):
    return _CREDS_RE.sub(r"\1", str(s))


def _looks_like_ip(body):
    body = (body or "").strip()
    return body if body and len(body) < 45 and any(c.isdigit() for c in body) else ""


def _classify_error(e, timeout):
    """Turn a requests exception into an actionable message.

    niceproxy-style gateways authenticate then silently never deliver an exit IP
    when the account isn't usable — that hangs until the timeout, so a plain
    "Read timed out" is useless. And sending HTTP to a SOCKS5 server makes the
    server reset the connection, which reads as "can't reach host" — name the
    real cause so the user fixes the scheme."""
    msg = _scrub(e)
    low = msg.lower()
    if isinstance(e, requests.exceptions.Timeout):
        return (f"No exit IP within {timeout}s — credentials were accepted but the gateway "
                "delivered nothing. Provider-side: account not activated, no traffic quota, "
                "your IP not whitelisted, or the residential pool is down.")
    if isinstance(e, (requests.exceptions.ProxyError, requests.exceptions.ConnectionError)):
        if "reset" in low or "broken pipe" in low:
            return ("Connection reset by proxy — if this is a SOCKS5 proxy, set scheme = socks5 "
                    "(not http). " + msg[:70])
        return "Can't reach the proxy host (network/firewall): " + msg[:90]
    return msg[:160] or type(e).__name__


def diagnose_proxy(host, port, username="", password="", scheme="http", timeout=20):
    """Return (results, ms_total).

    results = [{target, ok, ip, ms, error}] for HTTP then HTTPS. `ok` True means
    that path returned an IP through the proxy. Never logs the password — error
    strings are scrubbed of the embedded credentials.
    """
    scheme = scheme or "http"
    auth = f"{username}:{password}@" if username else ""
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}
    _log.info("diagnose start: %s://%s:%s (auth=%s, timeout=%ss)",
              scheme, host, port, bool(username), timeout)

    results = []
    t0 = time.monotonic()
    for url, label in _DIAG_TARGETS:
        ts = time.monotonic()
        try:
            resp = requests.get(url, proxies=proxies, timeout=timeout, allow_redirects=False)
        except Exception as e:
            ms = int((time.monotonic() - ts) * 1000)
            err = _classify_error(e, timeout)
            _log.warning("diagnose %s FAIL: %s (%dms)", label, _scrub(e)[:120], ms)
            results.append({"target": label, "ok": False, "ip": "", "ms": ms, "error": err})
            continue
        ms = int((time.monotonic() - ts) * 1000)
        body = (resp.text or "").strip()
        ip = _looks_like_ip(body)
        if ip:
            err = ""
        elif resp.status_code in (401, 407):
            err = "Auth failed — wrong username or password."
        else:
            err = f"HTTP {resp.status_code}: {body[:60]!r}"
        results.append({"target": label, "ok": bool(ip), "ip": ip, "ms": ms, "error": err})
        if ip:
            _log.info("diagnose %s OK: ip=%s (%dms)", label, ip, ms)
        else:
            _log.warning("diagnose %s no-IP: HTTP %s %r (%dms)", label, resp.status_code, body[:60], ms)
    return results, int((time.monotonic() - t0) * 1000)


# back-compat single-result helper for callers that only want the HTTPS verdict
def check_proxy(host, port, username="", password="", scheme="http", timeout=20):
    results, ms = diagnose_proxy(host, port, username, password, scheme, timeout)
    https = next((r for r in results if r["target"] == "HTTPS"), {})
    ok = bool(https.get("ok"))
    return ok, https.get("ip", ""), https.get("error", ""), ms, results


if __name__ == "__main__":
    # ponytail: dead target -> every probe fails fast with a connection error.
    results, ms = diagnose_proxy("127.0.0.1", 1, scheme="http", timeout=5)
    assert all(not r["ok"] for r in results) and len(results) == 2, results
    assert all("@" not in r["error"] for r in results), "credentials leaked into error"
    print(f"proxy self-check ok ({len(results)} targets, all failed) in {ms}ms")

"""Local SOCKS5 bridge: an unauthenticated SOCKS5 face in front of an
authenticated upstream SOCKS5 proxy, so Chromium can use it.

Why: Chromium cannot authenticate to a SOCKS5 proxy (hard browser limit) — it
only supports HTTP-proxy auth, or SOCKS5 with NO auth. HypeProxy-style providers
hand out SOCKS5+user:pass endpoints. This bridge accepts Chromium's no-auth
SOCKS5 connection on 127.0.0.1 and forwards each CONNECT through the real
upstream (PySocks attaches the credentials). One daemon-thread server per
distinct upstream, cached; dies with the process. Only the bot's browser uses
this — the checker talks SOCKS5 directly via requests.
"""
import logging
import select
import socket
import threading

import socks  # PySocks

_log = logging.getLogger("server.socksbridge")
_lock = threading.Lock()
_bridges = {}  # (host, port, user, pw) -> local listening port


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socks peer closed")
        buf += chunk
    return buf


def _relay(a, b):
    """Copy bytes both ways until either side closes. select so one thread does it."""
    try:
        while True:
            r, _, _ = select.select([a, b], [], [])
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def _handle(client, host, port, user, pw):
    try:
        # 1) greeting: VER, NMETHODS, METHODS — we always answer no-auth (0x00)
        hdr = _recv_exact(client, 2)
        if hdr[0] != 5:
            return
        _recv_exact(client, hdr[1])
        client.sendall(b"\x05\x00")

        # 2) request: VER, CMD, RSV, ATYP, DST.ADDR, DST.PORT (CONNECT only)
        req = _recv_exact(client, 4)
        if req[0] != 5 or req[1] != 1:
            client.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")  # cmd not supported
            return
        atyp = req[3]
        if atyp == 1:
            dst = socket.inet_ntoa(_recv_exact(client, 4))
        elif atyp == 3:
            ln = _recv_exact(client, 1)[0]
            dst = _recv_exact(client, ln).decode("utf-8", "replace")
        elif atyp == 4:
            dst = socket.inet_ntop(socket.AF_INET6, _recv_exact(client, 16))
        else:
            client.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")  # atyp unsupported
            return
        dport = int.from_bytes(_recv_exact(client, 2), "big")

        # 3) open the target through the authenticated upstream SOCKS5
        up = socks.socksocket()
        up.set_proxy(socks.SOCKS5, host, port, username=user or None, password=pw or None)
        up.settimeout(30)
        up.connect((dst, dport))
        client.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")  # succeeded
        _relay(client, up)
    except Exception as e:
        try:
            client.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")  # general failure
        except OSError:
            pass
        _log.debug("bridge: upstream connect failed (%s)", str(e)[:90])
    finally:
        try:
            client.close()
        except OSError:
            pass


def _serve(server_sock, host, port, user, pw):
    while True:
        try:
            client, _ = server_sock.accept()
        except OSError:
            return
        threading.Thread(target=_handle, args=(client, host, port, user, pw),
                         daemon=True).start()


def local_port_for(host, port, username="", password=""):
    """Local 127.0.0.1 port of a no-auth SOCKS5 bridge to the authed upstream.
    Starts one if needed; cached per upstream. Chromium connects here with no
    credentials."""
    key = (host, int(port), username or "", password or "")
    with _lock:
        if key in _bridges:
            return _bridges[key]
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(64)
    lp = s.getsockname()[1]
    threading.Thread(target=_serve, args=(s, host, int(port), username, password),
                     daemon=True).start()
    with _lock:
        if key in _bridges:  # race: another thread set it first
            s.close()
            return _bridges[key]
        _bridges[key] = lp
    _log.info("bridge up: 127.0.0.1:%s -> socks5://%s:%s (auth)", lp, host, port)
    return lp


if __name__ == "__main__":
    # ponytail: dead upstream -> greeting answers, CONNECT replies general-failure.
    lp = local_port_for("127.0.0.1", 1, "u", "p")
    c = socket.create_connection(("127.0.0.1", lp), timeout=5)
    c.sendall(b"\x05\x01\x00")  # greeting: 1 method, no-auth
    assert c.recv(2) == b"\x05\x00", "greeting not no-auth"
    c.sendall(b"\x05\x01\x00\x03\x0bexample.com\x00\x50")  # CONNECT example.com:80
    rep = c.recv(10)
    assert rep[:2] == b"\x05\x01", rep  # general SOCKS failure (upstream dead)
    c.close()
    print(f"socksbridge self-check ok on 127.0.0.1:{lp} (greeting + failure reply)")

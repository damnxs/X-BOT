"""Self-check for per-account fingerprints: persistent per account, unique
across accounts, coherent (UA OS matches navigator.platform), and every context
kwarg is a real Playwright option. No browser needed."""
import os
import tempfile

from xbot import fingerprint

with tempfile.TemporaryDirectory() as root:
    logs = os.path.join(root, "logs")
    os.makedirs(logs)

    # persistence: same account -> identical fingerprint on every session
    a1 = fingerprint.load_or_create(os.path.join(logs, "1"))
    a2 = fingerprint.load_or_create(os.path.join(logs, "1"))
    assert a1 == a2

    # uniqueness: one account per profile — no two accounts share a fingerprint
    fps = [a1] + [fingerprint.load_or_create(os.path.join(logs, str(i)))
                  for i in range(2, len(fingerprint.PROFILES) + 1)]
    keys = {fingerprint._key(fp) for fp in fps}
    assert len(keys) == len(fps) == len(fingerprint.PROFILES)

    # coherence + kwarg validity
    valid = {"user_agent", "viewport", "screen", "locale", "timezone_id",
             "color_scheme", "device_scale_factor"}
    for fp in fps:
        kw = fingerprint.context_kwargs(fp)
        assert set(kw) == valid
        win = "Windows NT" in fp["user_agent"]
        assert fp["platform"] == ("Win32" if win else "MacIntel")
        assert fp["screen"]["width"] >= fp["viewport"]["width"]
        assert fp["screen"]["height"] > fp["viewport"]["height"]

    # init script carries THIS account's values
    s = fingerprint.init_script(a1)
    assert f'"{a1["platform"]}"' in s and str(a1["hardware_concurrency"]) in s

    # pool overflow (accounts > profiles): still distinct from every other
    # account AND stable across sessions
    overs = []
    for i in range(3):
        d = os.path.join(logs, f"overflow{i}")
        o1 = fingerprint.load_or_create(d)
        o2 = fingerprint.load_or_create(d)
        assert o1 == o2                      # stable per account
        assert fingerprint._key(o1) not in keys, "overflow account collided"
        keys.add(fingerprint._key(o1))
        overs.append(o1)
    assert len({fingerprint._key(o) for o in overs}) == 3

print(f"fingerprint self-check OK — {len(fingerprint.PROFILES)} profiles, "
      f"{len(fps)} accounts all distinct")

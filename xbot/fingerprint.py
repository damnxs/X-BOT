"""Per-account persistent browser fingerprints.

One fingerprint per account, generated ONCE, stored in the account's log dir
(<log_dir>/fingerprint.json — same per-account state pattern as seen.json /
followed.json) and reused verbatim on every later session. A new account picks
a profile no sibling account already uses, so accounts never share fingerprints
while each account's own fingerprint never changes.

Only what Playwright can emulate natively is set here (UA, viewport, screen,
locale, timezone, color scheme, device scale factor) plus the navigator.* values
new_context() can't take (platform, CPU cores, memory) via an init script —
kept in the same stored profile so they stay coherent with the UA.
"""
import hashlib
import json
import os

# locale <-> timezone pairs are atomic: a German locale on a New York clock is
# a louder bot tell than any single value.
_REGIONS = [
    ("en-US", "America/New_York"), ("en-US", "America/Chicago"),
    ("en-US", "America/Denver"), ("en-US", "America/Los_Angeles"),
    ("en-US", "America/Phoenix"),
    ("en-GB", "Europe/London"),
    ("en-CA", "America/Toronto"), ("en-CA", "America/Vancouver"),
    ("en-AU", "Australia/Sydney"),
    ("de-DE", "Europe/Berlin"), ("fr-FR", "Europe/Paris"),
    ("es-ES", "Europe/Madrid"), ("nl-NL", "Europe/Amsterdam"),
    ("id-ID", "Asia/Jakarta"), ("en-SG", "Asia/Singapore"),
    ("en-IN", "Asia/Kolkata"), ("pt-BR", "America/Sao_Paulo"),
    ("en-PH", "Asia/Manila"), ("en-NZ", "Pacific/Auckland"),
    ("sv-SE", "Europe/Stockholm"),
]

# per desktop OS: UA template, navigator.platform, device_scale_factor, and
# (viewport_w, viewport_h, screen_w, screen_h) — viewport is the typical
# maximized window, screen the native panel (taskbar/browser chrome eats height).
_DESKTOPS = {
    "win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36",
        "Win32", 1,
        [(1920, 1048, 1920, 1080), (1536, 794, 1536, 864),
         (1366, 728, 1366, 768), (1600, 856, 1600, 900)],
    ),
    "mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/{v}.0.0.0 Safari/537.36",
        "MacIntel", 2,
        [(1440, 823, 1440, 900), (1512, 921, 1512, 982), (1680, 945, 1680, 1050)],
    ),
}

# Chrome versions plausibly in the wild at once (not everyone auto-updates)
_VERSIONS = [140, 139, 138, 137, 135, 133]
_CORES = [4, 8, 12, 16]
_MEMORY = [8, 8, 16, 8]

PROFILES = []
for _i, (_locale, _tz) in enumerate(_REGIONS):
    _ua_tpl, _plat, _dsf, _geoms = _DESKTOPS[("win", "mac")[_i % 2]]
    _vw, _vh, _sw, _sh = _geoms[_i % len(_geoms)]
    PROFILES.append({
        "user_agent": _ua_tpl.format(v=_VERSIONS[_i % len(_VERSIONS)]),
        "viewport": {"width": _vw, "height": _vh},
        "screen": {"width": _sw, "height": _sh},
        "locale": _locale,
        "timezone_id": _tz,
        "color_scheme": ("light", "dark")[_i % 2],
        "device_scale_factor": _dsf,
        "platform": _plat,
        "hardware_concurrency": _CORES[_i % len(_CORES)],
        "device_memory": _MEMORY[_i % len(_MEMORY)],
    })

# keys browser.new_context() accepts; the rest goes through init_script()
_CTX_KEYS = ("user_agent", "viewport", "screen", "locale", "timezone_id",
             "color_scheme", "device_scale_factor")


def _key(fp):
    return json.dumps(fp, sort_keys=True)


def _used_keys(log_dir):
    """Fingerprint identities already assigned to OTHER accounts (sibling
    logs/<id> dirs). Skips unreadable/stray entries; misses nothing that matters."""
    used = set()
    parent = os.path.dirname(os.path.abspath(log_dir))
    mine = os.path.basename(os.path.abspath(log_dir))
    try:
        names = os.listdir(parent)
    except OSError:
        return used
    for name in names:
        if name == mine:
            continue
        try:
            with open(os.path.join(parent, name, "fingerprint.json")) as f:
                used.add(_key(json.load(f)))
        except (OSError, ValueError):
            continue
    return used


def _variant(base, log_dir, n):
    """Deterministic nth variant of a base profile: a slightly smaller,
    non-maximized window (reads human; screen stays the native panel)."""
    h = int(hashlib.md5(f"{log_dir}#{n}".encode()).hexdigest(), 16)
    fp = dict(base)
    fp["viewport"] = {"width": base["viewport"]["width"] - 8 * (h % 24),
                      "height": base["viewport"]["height"] - 4 * ((h >> 8) % 24)}
    return fp


def _pick(log_dir):
    """A fingerprint no other account already has — guaranteed, at any account
    count. Fresh pool profile when one is free; otherwise deterministic
    variants of a hash-picked base, walking until one is unused."""
    used = _used_keys(log_dir)
    for fp in PROFILES:
        if _key(fp) not in used:
            return fp
    # ponytail: pool exhausted (accounts > profiles) — 576 variants per base;
    # grow _REGIONS if the swarm ever gets that big
    base = PROFILES[int(hashlib.md5(log_dir.encode()).hexdigest(), 16) % len(PROFILES)]
    for n in range(1, 600):
        fp = _variant(base, log_dir, n)
        if _key(fp) not in used:
            return fp
    return base  # unreachable in practice (575 variants all taken)


def load_or_create(log_dir):
    """The account's permanent fingerprint. Reads <log_dir>/fingerprint.json;
    generates + persists one only if absent/corrupt. Never regenerates otherwise."""
    path = os.path.join(log_dir, "fingerprint.json")
    try:
        with open(path) as f:
            fp = json.load(f)
        if fp.get("user_agent") and fp.get("viewport"):
            return fp
    except (OSError, ValueError):
        pass
    fp = _pick(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    with open(path, "w") as f:
        json.dump(fp, f, indent=2)
    return fp


def context_kwargs(fp):
    """The fingerprint as browser.new_context() kwargs (drops init-script fields)."""
    return {k: fp[k] for k in _CTX_KEYS}


def init_script(fp):
    """navigator.* values new_context() can't set. Without this, navigator.platform
    reports the HOST OS (e.g. 'Linux x86_64' on the VPS) under a Windows UA —
    an instant fingerprint mismatch. Runs before every page's scripts."""
    return (
        "Object.defineProperty(navigator, 'platform', {get: () => %s});"
        "Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => %d});"
        "Object.defineProperty(navigator, 'deviceMemory', {get: () => %d});"
    ) % (json.dumps(fp["platform"]), fp["hardware_concurrency"], fp["device_memory"])

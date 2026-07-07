"""SQLite storage (stdlib sqlite3, no ORM). Accounts, settings, runs."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from server import crypto

# Single source of truth for time: everything (scheduling, run timestamps, daily
# reset) is Asia/Jakarta so the UI shows consistent GMT+7 regardless of server tz.
TZ = ZoneInfo("Asia/Jakarta")

# ponytail: anchor to repo root (parent of server/) so the same DB is used no
# matter which cwd the server is launched from — otherwise a fresh empty
# data/bot.db appears elsewhere and accounts look "lost" across restarts.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "data", "bot.db")

DEFAULT_SETTINGS = {
    "schedule_active": "1",
    "day_start_hour": "0",
    "day_end_hour": "24",
    "dry_run": "1",
    "headless": "1",
    "like_probability": "0.6",
    "retweet_probability": "0.4",
    "reply_min_likes": "50",
    "reply_max_age_hours": "1",
    "min_delay_seconds": "30",
    "max_delay_seconds": "90",
    "openai_model": "gpt-4o-mini",
    "openai_post_system_prompt": (
        "You write original, engaging X/Twitter posts. Max 100 characters. "
        "On-topic. No quote marks, no emojis, no hashtags."
    ),
    "openai_reply_system_prompt": (
        "You write short, friendly, relevant X/Twitter replies. Max 100 characters. "
        "No quote marks, no @mentions, no emojis, no hashtags."
    ),
    "openai_api_key_enc": "",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  username TEXT DEFAULT '',
  auth_token_enc BLOB,
  ct0_enc BLOB,
  keywords TEXT DEFAULT '[]',
  mode TEXT DEFAULT 'search',
  daily_posts INTEGER DEFAULT 0,
  daily_likes INTEGER DEFAULT 0,
  daily_retweets INTEGER DEFAULT 0,
  daily_replies INTEGER DEFAULT 0,
  like_probability REAL DEFAULT 0.6,
  retweet_probability REAL DEFAULT 0.4,
  active INTEGER DEFAULT 1,
  proxy_id INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  status TEXT,
  counts TEXT DEFAULT '{}',
  run_log TEXT,
  trigger TEXT,
  exit_ip TEXT DEFAULT '',
  FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS scheduled_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  run_at TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT DEFAULT 'pending',
  run_id INTEGER,
  created_at TEXT,
  FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS proxies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER NOT NULL,
  scheme TEXT DEFAULT 'socks5',
  username TEXT DEFAULT '',
  password_enc BLOB,
  active INTEGER DEFAULT 1,
  alive INTEGER DEFAULT 0,
  last_ip TEXT DEFAULT '',
  last_checked_at TEXT DEFAULT '',
  last_error TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS proxy_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  proxy_id INTEGER,
  proxy_name TEXT,
  alive INTEGER,
  ip TEXT,
  scheme TEXT,
  error TEXT,
  detail TEXT,
  ms INTEGER,
  checked_at TEXT
);
"""


@contextmanager
def _conn():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        # migration: split the old single openai_system_prompt into post/reply,
        # preserving any value already saved. Runs before the default-seed loop so a
        # saved value wins over the new defaults; INSERT OR IGNORE leaves it untouched.
        old = c.execute(
            "SELECT value FROM settings WHERE key = 'openai_system_prompt'"
        ).fetchone()
        if old and old["value"]:
            for nk in ("openai_post_system_prompt", "openai_reply_system_prompt"):
                c.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                    (nk, old["value"]),
                )
        for k, v in DEFAULT_SETTINGS.items():
            c.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v)
            )
        # migrations for DBs created before a column existed
        for col in ("daily_replies",):
            try:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {col} INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # column already exists
        # proxy binding (nullable FK -> proxies.id); no default so existing rows stay NULL
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN proxy_id INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE proxies ADD COLUMN scheme TEXT DEFAULT 'socks5'")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE runs ADD COLUMN exit_ip TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass


# ---- accounts ---------------------------------------------------------

def _row_to_account(row, decrypt_cookies=False):
    d = dict(row)
    d["keywords"] = json.loads(d.get("keywords") or "[]")
    d["active"] = bool(d["active"])
    if decrypt_cookies:
        d["auth_token"] = crypto.decrypt(d.pop("auth_token_enc", b""))
        d["ct0"] = crypto.decrypt(d.pop("ct0_enc", b""))
    else:
        enc_at = d.pop("auth_token_enc", b"") or b""
        enc_ct = d.pop("ct0_enc", b"") or b""
        d["has_cookies"] = bool(enc_at) and bool(enc_ct)
    return d


def list_accounts(active_only=False):
    q = "SELECT * FROM accounts"
    if active_only:
        q += " WHERE active = 1"
    q += " ORDER BY id"
    with _conn() as c:
        rows = c.execute(q).fetchall()
    return [_row_to_account(r) for r in rows]


def get_account(account_id, decrypt_cookies=False):
    with _conn() as c:
        row = c.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return _row_to_account(row, decrypt_cookies) if row else None


def create_account(data):
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO accounts
               (name, username, auth_token_enc, ct0_enc, keywords, mode,
                daily_posts, daily_likes, daily_retweets, daily_replies,
                like_probability, retweet_probability, active, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["name"], data.get("username", ""),
                crypto.encrypt(data.get("auth_token", "")),
                crypto.encrypt(data.get("ct0", "")),
                json.dumps(data.get("keywords", [])),
                data.get("mode", "search"),
                int(data.get("daily_posts", 0)), int(data.get("daily_likes", 0)),
                int(data.get("daily_retweets", 0)), int(data.get("daily_replies", 0)),
                float(data.get("like_probability", 0.6)),
                float(data.get("retweet_probability", 0.4)),
                int(bool(data.get("active", True))),
                datetime.now(TZ).isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def update_account(account_id, data):
    # Build SET clause only for provided fields; cookies only if non-empty.
    fields, params = [], []
    for key, col in [("name", "name"), ("username", "username"), ("mode", "mode"),
                     ("daily_posts", "daily_posts"), ("daily_likes", "daily_likes"),
                     ("daily_retweets", "daily_retweets"), ("daily_replies", "daily_replies"),
                     ("like_probability", "like_probability"),
                     ("retweet_probability", "retweet_probability"),
                     ("proxy_id", "proxy_id")]:
        if key in data:
            fields.append(f"{col} = ?")
            params.append(data[key])
    if "keywords" in data:
        fields.append("keywords = ?")
        params.append(json.dumps(data["keywords"]))
    if "active" in data:
        fields.append("active = ?")
        params.append(int(bool(data["active"])))
    if data.get("auth_token"):
        fields.append("auth_token_enc = ?")
        params.append(crypto.encrypt(data["auth_token"]))
    if data.get("ct0"):
        fields.append("ct0_enc = ?")
        params.append(crypto.encrypt(data["ct0"]))
    if not fields:
        return
    params.append(account_id)
    with _conn() as c:
        c.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", params)


def delete_account(account_id):
    with _conn() as c:
        c.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


# ---- settings ---------------------------------------------------------

def get_settings(decrypt=False):
    with _conn() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    s = {r["key"]: r["value"] for r in rows}
    # normalize booleans to "1"/"0" (repairs any stale "True"/"False")
    for k in ("dry_run", "headless", "schedule_active"):
        if k in s:
            s[k] = "1" if str(s[k]).strip().lower() in ("1", "true") else "0"
    enc = s.pop("openai_api_key_enc", "")
    if decrypt:
        s["openai_api_key"] = crypto.decrypt(enc)
    else:
        s["openai_api_key_set"] = bool(enc)
    return s


def update_settings(data):
    # Booleans must be stored as "1"/"0": str(True) -> "True" would later fail
    # the `== "1"` checks in the scheduler and the frontend's boolVal().
    BOOL_KEYS = {"dry_run", "headless", "schedule_active"}
    updates = {}
    for k in ["schedule_active", "day_start_hour", "day_end_hour",
              "dry_run", "headless", "like_probability", "retweet_probability",
              "reply_min_likes", "reply_max_age_hours",
              "min_delay_seconds", "max_delay_seconds", "openai_model",
              "openai_post_system_prompt", "openai_reply_system_prompt"]:
        if k in data:
            v = data[k]
            updates[k] = ("1" if v else "0") if k in BOOL_KEYS else str(v)
    if "openai_api_key" in data and data["openai_api_key"]:
        updates["openai_api_key_enc"] = crypto.encrypt(data["openai_api_key"])
    with _conn() as c:
        for k, v in updates.items():
            c.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (k, v),
            )


# ---- proxies ---------------------------------------------------------

def _row_to_proxy(row, decrypt=False):
    d = dict(row)
    enc = d.pop("password_enc", b"") or b""
    if decrypt:
        d["password"] = crypto.decrypt(enc)
    else:
        d["has_password"] = bool(enc)
    d["active"] = bool(d.get("active"))
    d["alive"] = bool(d.get("alive"))
    return d


def list_proxies(decrypt=False):
    with _conn() as c:
        rows = c.execute("SELECT * FROM proxies ORDER BY id").fetchall()
    return [_row_to_proxy(r, decrypt) for r in rows]


def get_proxy(proxy_id, decrypt=False):
    with _conn() as c:
        row = c.execute("SELECT * FROM proxies WHERE id = ?", (proxy_id,)).fetchone()
    return _row_to_proxy(row, decrypt) if row else None


def _next_proxy_number():
    """Next auto-number for an unnamed proxy: max existing 'proxy N' + 1 (default 1)."""
    with _conn() as c:
        rows = c.execute("SELECT name FROM proxies WHERE name LIKE 'proxy %'").fetchall()
    nums = []
    for r in rows:
        tail = r["name"][len("proxy "):].strip()
        try:
            nums.append(int(tail))
        except ValueError:
            pass
    return (max(nums) + 1) if nums else 1


def create_proxy(data):
    name = (data.get("name") or "").strip()
    if not name:
        name = f"proxy {_next_proxy_number()}"
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO proxies
               (name, host, port, scheme, username, password_enc, active, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                name, data["host"], int(data["port"]),
                data.get("scheme") or "http",
                data.get("username", ""),
                crypto.encrypt(data.get("password", "")),
                int(bool(data.get("active", True))),
                datetime.now(TZ).isoformat(timespec="seconds"),
            ),
        )
        return cur.lastrowid


def update_proxy(proxy_id, data):
    fields, params = [], []
    for key in ("name", "host", "username", "scheme"):
        if key in data:
            fields.append(f"{key} = ?")
            params.append(data[key])
    if "port" in data:
        fields.append("port = ?")
        params.append(int(data["port"]))
    if "active" in data:
        fields.append("active = ?")
        params.append(int(bool(data["active"])))
    if data.get("password"):           # only overwrite when a new one is supplied
        fields.append("password_enc = ?")
        params.append(crypto.encrypt(data["password"]))
    if not fields:
        return
    params.append(proxy_id)
    with _conn() as c:
        c.execute(f"UPDATE proxies SET {', '.join(fields)} WHERE id = ?", params)


def delete_proxy(proxy_id):
    with _conn() as c:
        # unbind any accounts pointing at this proxy before removing it
        c.execute("UPDATE accounts SET proxy_id = NULL WHERE proxy_id = ?", (proxy_id,))
        c.execute("DELETE FROM proxies WHERE id = ?", (proxy_id,))


def record_proxy_check(proxy_id, alive, ip, error, scheme="", detail="", ms=0, proxy_name=""):
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with _conn() as c:
        # update the proxy's last-known status; only adopt a scheme on a SUCCESS so a
        # later failed check can't clobber the protocol that last worked.
        if scheme and alive:
            c.execute(
                "UPDATE proxies SET alive=?, last_ip=?, last_error=?, last_checked_at=?, scheme=? "
                "WHERE id=?",
                (int(bool(alive)), ip or "", error or "", now, scheme, proxy_id),
            )
        else:
            c.execute(
                "UPDATE proxies SET alive=?, last_ip=?, last_error=?, last_checked_at=? WHERE id=?",
                (int(bool(alive)), ip or "", error or "", now, proxy_id),
            )
        c.execute(
            """INSERT INTO proxy_log
               (proxy_id, proxy_name, alive, ip, scheme, error, detail, ms, checked_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (proxy_id, proxy_name, int(bool(alive)), ip or "", scheme or "",
             error or "", detail, int(ms or 0), now),
        )


def list_proxy_log(limit=50):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM proxy_log ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]


# ---- runs -------------------------------------------------------------

def record_run(account_id, started_at, finished_at, status, counts, run_log, trigger, exit_ip=""):
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO runs(account_id, started_at, finished_at, status, counts, run_log, trigger, exit_ip)
               VALUES (?,?,?,?,?,?,?,?)""",
            (account_id, started_at, finished_at, status, json.dumps(counts or {}), run_log, trigger, exit_ip or ""),
        )
        return cur.lastrowid


def list_runs(account_id, limit=20):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE account_id = ? ORDER BY id DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counts"] = json.loads(d.get("counts") or "{}")
        out.append(d)
    return out


def recent_activity(limit=50):
    """Newest runs across ALL accounts — backs the live activity feed."""
    with _conn() as c:
        rows = c.execute(
            """SELECT r.id, r.account_id, a.name AS account_name,
                      r.started_at, r.finished_at, r.status, r.counts, r.trigger, r.exit_ip
               FROM runs r
               LEFT JOIN accounts a ON a.id = r.account_id
               ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counts"] = json.loads(d.get("counts") or "{}")
        out.append(d)
    return out


def todays_counts(account_id):
    """Sum of successful post/like/retweet counts for this account since local midnight."""
    midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    totals = {"posts": 0, "likes": 0, "retweets": 0, "replies": 0}
    with _conn() as c:
        rows = c.execute(
            "SELECT counts FROM runs WHERE account_id = ? AND started_at >= ?",
            (account_id, midnight),
        ).fetchall()
    for r in rows:
        ct = json.loads(r["counts"] or "{}")
        totals["posts"] += int(ct.get("post", 0))
        totals["likes"] += int(ct.get("like", 0))
        totals["retweets"] += int(ct.get("retweet", 0))
        totals["replies"] += int(ct.get("reply", 0))
    return totals


# ---- scheduled_actions (daily randomized plan) ------------------------

def account_has_plan(account_id, date_str):
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM scheduled_actions WHERE account_id = ? AND date = ? LIMIT 1",
            (account_id, date_str),
        ).fetchone()
    return row is not None


def delete_pending(account_id, date_str):
    with _conn() as c:
        c.execute(
            "DELETE FROM scheduled_actions WHERE account_id = ? AND date = ? AND status = 'pending'",
            (account_id, date_str),
        )


def insert_scheduled(account_id, date_str, run_at, action_type):
    with _conn() as c:
        c.execute(
            "INSERT INTO scheduled_actions(account_id, date, run_at, action_type, status, created_at) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (account_id, date_str, run_at, action_type, datetime.now(TZ).isoformat(timespec="seconds")),
        )


def insert_next_if_idle(account_id, date_str, run_at, action_type):
    """Atomically insert the next pending action only if none is pending/running.

    The chained scheduler decides the next gap when the worker runs; this guard
    (check+insert in one transaction) prevents duplicate chains if the poll tick
    and the worker race.
    """
    with _conn() as c:
        in_flight = c.execute(
            "SELECT 1 FROM scheduled_actions WHERE account_id = ? AND date = ? "
            "AND status IN ('pending','running') LIMIT 1",
            (account_id, date_str),
        ).fetchone()
        if in_flight:
            return False
        c.execute(
            "INSERT INTO scheduled_actions(account_id, date, run_at, action_type, status, created_at) "
            "VALUES (?,?,?,?, 'pending', ?)",
            (account_id, date_str, run_at, action_type, datetime.now(TZ).isoformat(timespec="seconds")),
        )
        return True


def due_actions(now_iso):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM scheduled_actions WHERE status = 'pending' AND run_at <= ? ORDER BY run_at",
            (now_iso,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_scheduled(sa_id, status, run_id=None):
    with _conn() as c:
        c.execute(
            "UPDATE scheduled_actions SET status = ?, run_id = ? WHERE id = ?",
            (status, run_id, sa_id),
        )


def list_account_schedule(account_id, date_str, limit=200):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM scheduled_actions WHERE account_id = ? AND date = ? ORDER BY run_at LIMIT ?",
            (account_id, date_str, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def next_pending(account_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM scheduled_actions WHERE account_id = ? AND status = 'pending' "
            "ORDER BY run_at LIMIT 1",
            (account_id,),
        ).fetchone()
    return dict(row) if row else None

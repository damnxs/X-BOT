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
  pop_min_likes INTEGER DEFAULT 0,
  pop_min_replies INTEGER DEFAULT 0,
  pop_tab TEXT DEFAULT 'live',
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
  action_type TEXT DEFAULT '',
  delay_until TEXT DEFAULT '',
  tweet_id TEXT DEFAULT '',
  raid_id INTEGER,
  FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS raids (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tweet_id TEXT NOT NULL,
  kind TEXT DEFAULT 'single',
  status TEXT DEFAULT 'running',
  requested TEXT DEFAULT '{}',
  summary TEXT DEFAULT '{}',
  started_at TEXT,
  finished_at TEXT DEFAULT '',
  created_at TEXT
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
CREATE TABLE IF NOT EXISTS ar_accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id INTEGER NOT NULL UNIQUE,
  status TEXT DEFAULT 'draft',
  config TEXT DEFAULT '{}',
  next_run_at TEXT DEFAULT '',
  last_run_at TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS ar_tweets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ar_id INTEGER NOT NULL,
  account_id INTEGER NOT NULL,
  tweet_id TEXT NOT NULL,
  url TEXT DEFAULT '',
  author TEXT DEFAULT '',
  content TEXT DEFAULT '',
  keyword TEXT DEFAULT '',
  likes INTEGER DEFAULT 0,
  retweets INTEGER DEFAULT 0,
  replies INTEGER DEFAULT 0,
  reply_text TEXT DEFAULT '',
  status TEXT DEFAULT 'skipped',
  reason TEXT DEFAULT '',
  error TEXT DEFAULT '',
  created_at TEXT,
  replied_at TEXT DEFAULT '',
  UNIQUE(ar_id, tweet_id)
);
CREATE INDEX IF NOT EXISTS idx_ar_tweets_account ON ar_tweets(account_id);
CREATE TABLE IF NOT EXISTS ar_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ar_id INTEGER NOT NULL,
  account_id INTEGER NOT NULL,
  ts TEXT,
  level TEXT DEFAULT 'info',
  message TEXT
);
CREATE INDEX IF NOT EXISTS idx_ar_log_ar ON ar_log(ar_id);
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
        try:
            c.execute("ALTER TABLE runs ADD COLUMN action_type TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE runs ADD COLUMN delay_until TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE runs ADD COLUMN tweet_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE runs ADD COLUMN raid_id INTEGER")
        except sqlite3.OperationalError:
            pass
        # any run left 'running' from a crash/restart is stale -> mark it errored
        c.execute(
            "UPDATE runs SET status='error', "
            "counts='{\"error\":\"interrupted (server restart)\"}' WHERE status='running'"
        )
        # search-by-popularity mode thresholds + tab (only meaningful when mode='search_popularity')
        for col in ("pop_min_likes", "pop_min_replies"):
            try:
                c.execute(f"ALTER TABLE accounts ADD COLUMN {col} INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # column already exists
        try:
            c.execute("ALTER TABLE accounts ADD COLUMN pop_tab TEXT DEFAULT 'live'")
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
                like_probability, retweet_probability, active,
                pop_min_likes, pop_min_replies, pop_tab, proxy_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                int(data.get("pop_min_likes", 0) or 0),
                int(data.get("pop_min_replies", 0) or 0),
                (data.get("pop_tab") or "live"),
                int(data["proxy_id"]) if data.get("proxy_id") else None,
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
                     ("proxy_id", "proxy_id"),
                     ("pop_min_likes", "pop_min_likes"),
                     ("pop_min_replies", "pop_min_replies"),
                     ("pop_tab", "pop_tab")]:
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
        # also drop any auto-reply automation for this account — otherwise a live
        # zombie cycle keeps firing every interval with no account behind it
        c.execute("DELETE FROM ar_tweets WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM ar_log WHERE account_id = ?", (account_id,))
        c.execute("DELETE FROM ar_accounts WHERE account_id = ?", (account_id,))
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
              "reply_min_likes",
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


def clear_proxy_log():
    """Delete the entire proxy check history."""
    with _conn() as c:
        c.execute("DELETE FROM proxy_log")


# ---- runs -------------------------------------------------------------

def record_run(account_id, started_at, finished_at, status, counts, run_log, trigger, exit_ip=""):
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO runs(account_id, started_at, finished_at, status, counts, run_log, trigger, exit_ip)
               VALUES (?,?,?,?,?,?,?,?)""",
            (account_id, started_at, finished_at, status, json.dumps(counts or {}), run_log, trigger, exit_ip or ""),
        )
        return cur.lastrowid


def start_run(account_id, started_at, trigger, action_type="", tweet_id=""):
    """Insert a 'running' row the moment a run begins so the activity feed can show
    it as In Progress. Returns the new run id; finish_run() completes it."""
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO runs(account_id, started_at, status, counts, trigger, action_type, tweet_id)
               VALUES (?,?,'running','{}',?,?,?)""",
            (account_id, started_at, trigger, action_type or "", tweet_id or ""),
        )
        return cur.lastrowid


def finish_run(rid, finished_at, status, counts, run_log, exit_ip=""):
    """Fill in the result of a run started by start_run()."""
    with _conn() as c:
        c.execute(
            """UPDATE runs SET finished_at=?, status=?, counts=?, run_log=?, exit_ip=?, delay_until=''
               WHERE id=?""",
            (finished_at, status, json.dumps(counts or {}), run_log, exit_ip or "", rid),
        )


def set_run_delay(rid, delay_until):
    """Stamp the wall-clock time the pre-action delay ends, so the activity feed can
    show a live countdown while the run is 'running'."""
    with _conn() as c:
        c.execute("UPDATE runs SET delay_until=? WHERE id=?", (delay_until or "", rid))


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


def clear_error_runs():
    """Delete every errored run from the activity history (bug cleanup)."""
    with _conn() as c:
        c.execute("DELETE FROM runs WHERE status='error'")


def recent_activity(limit=50):
    """Newest WARM-UP runs — backs the warm-up page's live activity feed.
    Auto-reply cycles are excluded: they have their own feed (ar_log)."""
    with _conn() as c:
        rows = c.execute(
            """SELECT r.id, r.account_id, a.name AS account_name,
                      r.started_at, r.finished_at, r.status, r.counts, r.trigger,
                      r.exit_ip, r.run_log, r.action_type, r.delay_until
               FROM runs r
               LEFT JOIN accounts a ON a.id = r.account_id
               WHERE r.action_type != 'autoreply'
               ORDER BY r.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counts"] = json.loads(d.get("counts") or "{}")
        out.append(d)
    return out


def get_run(rid):
    """Single run row (for the run-log endpoint). counts parsed to a dict."""
    with _conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["counts"] = json.loads(d.get("counts") or "{}")
    return d


def last_activity_per_account():
    """Latest successful action (action_type + started_at) per account, in one
    query — powers the Raider 'Last liked 29 Min Ago' column."""
    with _conn() as c:
        rows = c.execute(
            """SELECT m.account_id, r.action_type, r.started_at
               FROM (SELECT account_id, MAX(id) AS max_id
                     FROM runs WHERE status='ok' AND action_type != ''
                     GROUP BY account_id) m
               JOIN runs r ON r.id = m.max_id"""
        ).fetchall()
    return {r["account_id"]: {"action": r["action_type"], "ts": r["started_at"] or ""}
            for r in rows}


def last_action_ts_by_type():
    """Latest successful ts per (account_id, action_type) — one query. Powers the
    Mass Raid 'oldest last action first' rotation."""
    with _conn() as c:
        rows = c.execute(
            """SELECT m.account_id, m.action_type, r.started_at
               FROM (SELECT account_id, action_type, MAX(id) AS max_id
                     FROM runs WHERE status='ok' AND action_type != ''
                     GROUP BY account_id, action_type) m
               JOIN runs r ON r.id = m.max_id"""
        ).fetchall()
    return {(r["account_id"], r["action_type"]): r["started_at"] or ""
            for r in rows}


def accounts_that_did_action(tweet_id, action):
    """Set of account_ids that have a successful run of `action` on `tweet_id`
    (dedup: don't re-engage the same account on the same tweet)."""
    with _conn() as c:
        rows = c.execute(
            """SELECT DISTINCT account_id FROM runs
               WHERE status='ok' AND action_type=? AND tweet_id=?""",
            (action, tweet_id),
        ).fetchall()
    return {r["account_id"] for r in rows}


def running_account_ids():
    """Account ids with a run still in 'running' state (busy)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT DISTINCT account_id FROM runs WHERE status='running'"
        ).fetchall()
    return {r["account_id"] for r in rows}


# ---- raids (a raid = one execution: single = 1 run, mass = many) -------

def start_raid(tweet_id, kind="single", requested=None):
    """Create a raid row (status='running'). Returns its id."""
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO raids(tweet_id, kind, status, requested, summary, started_at, created_at)
               VALUES(?,?,'running',?,'{}',?,?)""",
            (tweet_id, kind, json.dumps(requested or {}), now, now),
        )
        return cur.lastrowid


def set_run_raid(run_id, raid_id):
    """Link a run to the raid it belongs to."""
    if not run_id or not raid_id:
        return
    with _conn() as c:
        c.execute("UPDATE runs SET raid_id=? WHERE id=?", (raid_id, run_id))


def bump_raid(raid_id, action, ok):
    """Increment the per-action ok/fail tally on a raid's live summary."""
    if not raid_id or action not in ("like", "retweet", "reply"):
        return
    with _conn() as c:
        row = c.execute("SELECT summary FROM raids WHERE id=?", (raid_id,)).fetchone()
        if not row:
            return
        summary = json.loads(row["summary"] or "{}")
        slot = summary.setdefault(action, {"ok": 0, "fail": 0})
        slot["ok" if ok else "fail"] += 1
        c.execute("UPDATE raids SET summary=? WHERE id=?", (json.dumps(summary), raid_id))


def finish_raid(raid_id, status="completed"):
    if not raid_id:
        return
    with _conn() as c:
        c.execute(
            "UPDATE raids SET status=?, finished_at=? WHERE id=?",
            (status, datetime.now(TZ).isoformat(timespec="seconds"), raid_id),
        )


def list_raids(limit=50):
    """Newest raids with their account roll-up (used + failed), in one pass."""
    with _conn() as c:
        raids = c.execute(
            "SELECT * FROM raids ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        if not raids:
            return []
        ids = [r["id"] for r in raids]
        # runs belonging to these raids, joined to accounts for the username
        runs = c.execute(
            """SELECT r.raid_id, r.action_type, r.status, r.counts, a.username
               FROM runs r LEFT JOIN accounts a ON a.id = r.account_id
               WHERE r.raid_id IN (%s) ORDER BY r.id""" % ",".join("?" * len(ids)),
            ids,
        ).fetchall()
    by_raid = {}
    for rw in runs:
        bucket = by_raid.setdefault(rw["raid_id"], {"accounts": [], "failed": []})
        username = rw["username"] or "?"
        if rw["status"] == "ok":
            bucket["accounts"].append({"username": username, "action": rw["action_type"]})
        elif rw["status"] == "error":
            msg = ""
            try:
                msg = (json.loads(rw["counts"] or "{}") or {}).get("error", "")
            except (ValueError, TypeError):
                pass
            bucket["failed"].append({"username": username, "action": rw["action_type"], "message": msg})
    out = []
    for r in raids:
        d = dict(r)
        d["requested"] = json.loads(d.get("requested") or "{}")
        d["summary"] = json.loads(d.get("summary") or "{}")
        d["accounts"] = (by_raid.get(r["id"]) or {}).get("accounts", [])
        d["failed"] = (by_raid.get(r["id"]) or {}).get("failed", [])
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


# ---- twitter auto reply -------------------------------------------------

# Per-account config (stored as JSON on ar_accounts.config). Everything the
# automation needs lives here — one account = one independent config.
AR_DEFAULT_CONFIG = {
    "keywords": [],
    "exclude_keywords": [],
    "lang": "",                 # '' = any language
    "min_likes": 50,
    "min_retweets": 10,
    "min_replies": 5,
    "max_age_hours": 24,
    "sort": "live",             # live = Latest tab, top = Top tab
    "tweets_per_cycle": 20,     # the ONLY reply cap — per cycle
    "interval_minutes": 10,     # search interval between cycles
    "cooldown_seconds": 20,     # gap between replies inside a cycle
    "max_reply_chars": 280,
    "banned_words": [],
    "system_prompt": (
        "Kamu adalah social media account yang fokus pada AI dan automation.\n\n"
        "Gaya komunikasi:\n"
        "- Natural, singkat, insightful\n"
        "- Tidak terlalu formal, tidak terdengar seperti bot\n"
        "- Hindari generic response\n\n"
        "Tujuan:\n"
        "- Memberikan value dalam setiap reply\n"
        "- Memulai conversation\n\n"
        "Rules:\n"
        "- Jangan mengatakan bahwa kamu adalah AI\n"
        "- Jangan menggunakan reply yang terlalu panjang\n"
        "- Jangan hard selling, jangan spam\n"
        "- Jangan mengulang kalimat yang sama\n"
        "- Jangan menggunakan hashtag kecuali diperlukan"
    ),
}


def _ar_row(row, with_stats=None):
    d = dict(row)
    d["config"] = {**AR_DEFAULT_CONFIG, **json.loads(d.get("config") or "{}")}
    for k, v in (with_stats or {}).items():
        d[k] = int(v or 0)
    return d


def ar_assign(account_id, config=None):
    """Assign a warm-up account to the auto-reply automation (status draft).
    Returns the new ar row id, or None if the account is already assigned."""
    with _conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO ar_accounts(account_id, status, config, created_at) VALUES (?,?,?,?)",
                (account_id, "draft",
                 json.dumps({**AR_DEFAULT_CONFIG, **(config or {})}),
                 datetime.now(TZ).isoformat(timespec="seconds")),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            return None


def ar_list():
    """Assigned accounts (config parsed) joined to their warm-up account info and
    per-account reply stats — powers the automation's accounts table."""
    midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _conn() as c:
        ars = c.execute(
            """SELECT ar.id, ar.id AS ar_id, ar.account_id, ar.status, ar.config,
                      -- ^ both keys: scheduler enqueue uses id, API rows use ar_id
                      ar.next_run_at, ar.last_run_at, ar.created_at,
                      a.name AS account_name, a.username,
                      a.active AS warmup_active,
                      (a.auth_token_enc IS NOT NULL AND a.ct0_enc IS NOT NULL) AS has_cookies
               FROM ar_accounts ar LEFT JOIN accounts a ON a.id = ar.account_id
               ORDER BY ar.id"""
        ).fetchall()
        stats = {
            r["ar_id"]: dict(r)   # dict, not Row — _ar_row calls .items() on it
            for r in c.execute(
                """SELECT ar_id, COUNT(*) AS found,
                          SUM(status = 'replied') AS replied,
                          SUM(status = 'failed') AS failed,
                          SUM(CASE WHEN status = 'replied' AND replied_at >= ? THEN 1 ELSE 0 END) AS today
                   FROM ar_tweets GROUP BY ar_id""",
                (midnight,),
            ).fetchall()
        }
    return [_ar_row(r, stats.get(r["ar_id"])) for r in ars]


def ar_get(ar_id):
    with _conn() as c:
        row = c.execute("SELECT * FROM ar_accounts WHERE id = ?", (ar_id,)).fetchone()
    return _ar_row(row) if row else None


def ar_get_by_account(account_id):
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM ar_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
    return _ar_row(row) if row else None


def ar_update(ar_id, data):
    """Update status and/or config. Activating an account with no next run
    scheduled kicks off its first cycle on the next tick."""
    fields, params = [], []
    if "status" in data:
        fields.append("status = ?")
        params.append(data["status"])
    if "config" in data:
        fields.append("config = ?")
        params.append(json.dumps(data["config"]))
    if not fields:
        return
    with _conn() as c:
        c.execute(f"UPDATE ar_accounts SET {', '.join(fields)} WHERE id = ?", [*params, ar_id])
        if data.get("status") == "active":
            c.execute(
                "UPDATE ar_accounts SET next_run_at = ? "
                "WHERE id = ? AND (next_run_at = '' OR next_run_at IS NULL)",
                (datetime.now(TZ).isoformat(timespec="seconds"), ar_id),
            )


def ar_set_next_run(ar_id, run_at_iso):
    with _conn() as c:
        c.execute("UPDATE ar_accounts SET next_run_at = ? WHERE id = ?", (run_at_iso, ar_id))


def ar_set_last_run(ar_id, ts_iso):
    with _conn() as c:
        c.execute("UPDATE ar_accounts SET last_run_at = ? WHERE id = ?", (ts_iso, ar_id))


def ar_delete(ar_id):
    """Unassign an account: drop its config row plus its tweet history and log
    (ar_tweets/ar_log carry no FK, so they're cleaned explicitly)."""
    with _conn() as c:
        c.execute("DELETE FROM ar_tweets WHERE ar_id = ?", (ar_id,))
        c.execute("DELETE FROM ar_log WHERE ar_id = ?", (ar_id,))
        c.execute("DELETE FROM ar_accounts WHERE id = ?", (ar_id,))


def ar_processed_ids(ar_id):
    """Tweet ids this automation already handled — the duplicate guard.
    Dry-run and LLM-error rows are excluded: those tweets were never actually
    replied to (or failed transiently), so they stay eligible for retry."""
    with _conn() as c:
        rows = c.execute(
            """SELECT tweet_id FROM ar_tweets WHERE ar_id = ?
               AND reason NOT LIKE 'dry_run%' AND reason NOT LIKE 'llm error%'""",
            (ar_id,),
        ).fetchall()
    return {r["tweet_id"] for r in rows}


def ar_record_tweets(ar_id, account_id, rows):
    """Persist one cycle's tweets. Rows deferred by the cycle limit aren't stored
    (they weren't processed). A later real reply attempt upgrades an earlier
    dry-run/failed-generation row; a settled replied/failed row never changes —
    a tweet is only ever replied to once."""
    now = datetime.now(TZ).isoformat(timespec="seconds")
    with _conn() as c:
        for r in rows or []:
            if (r.get("reason") or "") == "cycle limit":
                continue
            c.execute(
                """INSERT INTO ar_tweets
                   (ar_id, account_id, tweet_id, url, author, content, keyword,
                    likes, retweets, replies, reply_text, status, reason, error,
                    created_at, replied_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(ar_id, tweet_id) DO UPDATE SET
                     reply_text = excluded.reply_text, status = excluded.status,
                     reason = excluded.reason, error = excluded.error,
                     likes = excluded.likes, retweets = excluded.retweets,
                     replies = excluded.replies, replied_at = excluded.replied_at
                   WHERE ar_tweets.status NOT IN ('replied', 'failed')
                     AND excluded.status IN ('replied', 'failed')""",
                (ar_id, account_id, r.get("tweet_id", ""), r.get("url", ""),
                 r.get("author", ""), r.get("content", ""), r.get("keyword", ""),
                 int(r.get("likes") or 0), int(r.get("retweets") or 0),
                 int(r.get("replies") or 0), r.get("reply", ""),
                 r.get("status", "skipped"), r.get("reason", ""), r.get("error", ""),
                 now, now if r.get("status") == "replied" else ""),
            )


def ar_reset(ar_id):
    """Reset button: clear this automation's tweet history so every tweet is
    eligible again (duplicate guard + counters start from zero)."""
    with _conn() as c:
        cur = c.execute("DELETE FROM ar_tweets WHERE ar_id = ?", (ar_id,))
        return cur.rowcount


def ar_list_tweets(ar_id=None, status=None, date=None, q=None, limit=100):
    """Tweet history with filters: per automation row, reply status, calendar
    date (Jakarta), or free text over author/content/reply."""
    where, params = ["1=1"], []
    if ar_id:
        where.append("t.ar_id = ?")
        params.append(ar_id)
    if status:
        where.append("t.status = ?")
        params.append(status)
    if date:
        where.append("substr(t.created_at, 1, 10) = ?")  # Jakarta calendar date; date() would shift it to UTC
        params.append(date)
    if q:
        where.append("(t.content LIKE ? OR t.author LIKE ? OR t.reply_text LIKE ?)")
        params += [f"%{q}%"] * 3
    with _conn() as c:
        rows = c.execute(
            f"""SELECT t.*, a.username AS account_username
                FROM ar_tweets t LEFT JOIN accounts a ON a.id = t.account_id
                WHERE {' AND '.join(where)} ORDER BY t.id DESC LIMIT ?""",
            [*params, int(limit)],
        ).fetchall()
    return [dict(r) for r in rows]


def ar_stats():
    """Dashboard totals: found / qualified / generated / posted / failed + today
    + active automation count."""
    midnight = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with _conn() as c:
        row = c.execute(
            """SELECT COUNT(*) AS found,
                      SUM(status IN ('replied', 'failed')) AS qualified,
                      SUM(reply_text != '') AS generated,
                      SUM(status = 'replied') AS posted,
                      SUM(status = 'failed') AS failed
               FROM ar_tweets"""
        ).fetchone()
        today = c.execute(
            "SELECT COUNT(*) FROM ar_tweets WHERE status = 'replied' AND replied_at >= ?",
            (midnight,),
        ).fetchone()[0]
        active = c.execute(
            "SELECT COUNT(*) FROM ar_accounts WHERE status = 'active'"
        ).fetchone()[0]
    return {**{k: int(v or 0) for k, v in dict(row).items()},
            "today": int(today or 0), "active_accounts": int(active or 0)}


def ar_overview():
    """Everything the Twitter Auto Reply page needs in one call: dashboard
    stats, assigned accounts (with stats), and assignable warm-up accounts."""
    assigned_ids = {r["account_id"] for r in ar_list()}
    available = [
        {"id": a["id"], "name": a["name"], "username": a.get("username", ""),
         "active": a["active"], "has_cookies": a.get("has_cookies", False)}
        for a in list_accounts() if a["id"] not in assigned_ids
    ]
    return {"stats": ar_stats(), "accounts": ar_list(), "available": available}


def ar_log_add(ar_id, account_id, message, level="info"):
    with _conn() as c:
        c.execute(
            "INSERT INTO ar_log(ar_id, account_id, ts, level, message) VALUES (?,?,?,?,?)",
            (ar_id, account_id, datetime.now(TZ).isoformat(timespec="seconds"),
             level, message),
        )


def ar_log_list(ar_id, limit=200):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM ar_log WHERE ar_id = ? ORDER BY id DESC LIMIT ?",
            (ar_id, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def ar_log_all(limit=100):
    """Recent activity-log lines across ALL auto-reply accounts (newest first),
    joined to the @username — backs the live feed on the Auto Reply page."""
    with _conn() as c:
        rows = c.execute(
            """SELECT l.*, a.username AS account_username
               FROM ar_log l LEFT JOIN accounts a ON a.id = l.account_id
               ORDER BY l.id DESC LIMIT ?""",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]

"""SQLite storage (stdlib sqlite3, no ORM). Accounts, settings, runs."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from server import crypto

DB_PATH = os.path.join("data", "bot.db")

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
    "openai_system_prompt": "You write short, Max 100 character, friendly, on-topic X/Twitter messages. No quote marks. Without emoticon And Without Hastags",
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
                datetime.now().isoformat(timespec="seconds"),
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
                     ("retweet_probability", "retweet_probability")]:
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
              "min_delay_seconds", "max_delay_seconds", "openai_model", "openai_system_prompt"]:
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


# ---- runs -------------------------------------------------------------

def record_run(account_id, started_at, finished_at, status, counts, run_log, trigger):
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO runs(account_id, started_at, finished_at, status, counts, run_log, trigger)
               VALUES (?,?,?,?,?,?,?)""",
            (account_id, started_at, finished_at, status, json.dumps(counts or {}), run_log, trigger),
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


def todays_counts(account_id):
    """Sum of successful post/like/retweet counts for this account since local midnight."""
    midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
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
            (account_id, date_str, run_at, action_type, datetime.now().isoformat(timespec="seconds")),
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
            (account_id, date_str, run_at, action_type, datetime.now().isoformat(timespec="seconds")),
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

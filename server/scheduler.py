"""Scheduler: chained per-account runs with randomized gaps.

There is no fixed interval setting. The poll tick just (a) kicks off an account's
chain if it has remaining daily quota and nothing in flight, and (b) fires actions
whose time has come. When the worker finishes an action it schedules the NEXT one
at `now + randomized gap`, where the gap is computed to spread the remaining daily
quota across the rest of the day window. So the interval between actions is
decided at run time, not in advance.

scheduled_actions (DB) is the source of truth; one pending row per account at a
time. `insert_next_if_idle` makes check+insert atomic so the tick and the worker
can't create duplicate chains.
"""
import logging
import random
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from server import db
from xbot.runner import execute_action

log = logging.getLogger("xbot.scheduler")

POLL_MINUTES = 1  # internal cadence; not a user setting

_queue = deque()           # (account_id, action_type, trigger, scheduled_action_id)
_lock = threading.Lock()
_scheduler = None
_worker_started = False
_current = {"account_id": None, "name": None, "action": None, "trigger": None}


def _today():
    return datetime.now(db.TZ).strftime("%Y-%m-%d")


def start():
    global _scheduler, _worker_started
    if _scheduler:
        return
    db.init_db()
    _scheduler = BackgroundScheduler()
    _scheduler.start()
    _scheduler.add_job(_tick, "interval", minutes=POLL_MINUTES, id="tick",
                       max_instances=1, coalesce=True)
    if not _worker_started:
        threading.Thread(target=_worker_loop, daemon=True, name="xbot-worker").start()
        _worker_started = True
    log.info("scheduler started (poll every %s min)", POLL_MINUTES)


def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)


def reschedule():
    """Kept for API compatibility; cadence is a fixed internal constant now."""
    if not _scheduler:
        return
    _scheduler.remove_all_jobs()
    _scheduler.add_job(_tick, "interval", minutes=POLL_MINUTES, id="tick",
                       max_instances=1, coalesce=True)


def enqueue(account_id, action_type, trigger="manual", sa_id=None):
    with _lock:
        _queue.append((account_id, action_type, trigger, sa_id))


def run_tick_now():
    _tick(force=True)


# ---- chain planning ---------------------------------------------------

def _remaining_types(account):
    """Action types still owed today: e.g. ['post','like','retweet','retweet']."""
    done = db.todays_counts(account["id"])
    types = []
    for t, qkey, dkey in (("post", "daily_posts", "posts"),
                          ("like", "daily_likes", "likes"),
                          ("retweet", "daily_retweets", "retweets"),
                          ("reply", "daily_replies", "replies")):
        types += [t] * max(0, int(account.get(qkey, 0)) - done.get(dkey, 0))
    return types


def _window_bounds(settings):
    now = datetime.now(db.TZ)
    start_h = float(settings.get("day_start_hour", 0))
    end_h = float(settings.get("day_end_hour", 24))
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now, midnight + timedelta(hours=start_h), midnight + timedelta(hours=end_h)


def _first_run_at(settings):
    """Kickoff time: now (or day_start if earlier), + small jitter."""
    now, day_start, _ = _window_bounds(settings)
    return max(now, day_start) + timedelta(seconds=random.uniform(20, 240))


def _next_gap_seconds(account, settings):
    """Even gap = day window / TOTAL daily actions.

    e.g. 2 posts + 2 likes + 2 retweets = 6 actions over a 24h window -> ~4h apart.
    Based on the total quota (not remaining) so spacing stays even all day; a small
    ±10% jitter keeps it from firing on the exact dot.
    """
    total = (int(account.get("daily_posts", 0)) + int(account.get("daily_likes", 0))
             + int(account.get("daily_retweets", 0)))
    start_h = float(settings.get("day_start_hour", 0))
    end_h = float(settings.get("day_end_hour", 24))
    window_seconds = max(3600.0, (end_h - start_h) * 3600.0)
    if total <= 0:
        return random.uniform(600, 1800)
    return (window_seconds / total) * random.uniform(0.9, 1.1)


def _maybe_start_chain(account):
    types = _remaining_types(account)
    if not types:
        return 0
    settings = db.get_settings()
    db.insert_next_if_idle(account["id"], _today(), _first_run_at(settings).isoformat(),
                           random.choice(types))
    return 1


def schedule_next(account):
    """Called by the worker after a run: queue the next action at a randomized gap."""
    types = _remaining_types(account)
    if not types:
        return None
    settings = db.get_settings()
    run_at = datetime.now(db.TZ) + timedelta(seconds=_next_gap_seconds(account, settings))
    db.insert_next_if_idle(account["id"], _today(), run_at.isoformat(), random.choice(types))
    return run_at


def restart_chain(account):
    """Replan: drop pending and (re)start the chain. Returns remaining count."""
    db.delete_pending(account["id"], _today())
    remaining = len(_remaining_types(account))
    _maybe_start_chain(account)
    return remaining


def status():
    with _lock:
        q = list(_queue)
    today = _today()
    pending, done = 0, 0
    for acc in db.list_accounts():
        for sa in db.list_account_schedule(acc["id"], today):
            if sa["status"] == "pending":
                pending += 1
            elif sa["status"] in ("done", "error", "skipped"):
                done += 1
    active = db.get_settings().get("schedule_active", "1") == "1"
    return {
        "schedule_active": active,
        "running": _current["account_id"] is not None,
        "current": dict(_current) if _current["account_id"] else None,
        "queue_depth": len(q),
        "queue": [{"account_id": a, "action": ty, "trigger": tr} for a, ty, tr, _ in q],
        "today_pending": pending,
        "today_done": done,
    }


# ---- internals --------------------------------------------------------

def _tick(force=False):
    settings = db.get_settings()
    if not force and settings.get("schedule_active", "1") != "1":
        return
    now, day_start, _ = _window_bounds(settings)
    if now >= day_start:
        for acc in db.list_accounts(active_only=True):
            _maybe_start_chain(acc)
    now_iso = datetime.now(db.TZ).isoformat()
    for sa in db.due_actions(now_iso):
        db.mark_scheduled(sa["id"], "running")
        enqueue(sa["account_id"], sa["action_type"], "schedule", sa["id"])


def _worker_loop():
    while True:
        item = None
        with _lock:
            if _queue:
                item = _queue.popleft()
        if item is None:
            time.sleep(2)
            continue
        account_id, action_type, trigger, sa_id = item
        try:
            _run_one(account_id, action_type, trigger, sa_id)
        except Exception:
            log.exception("worker error on account %s action %s", account_id, action_type)


def _resolve_proxy(proxy_id):
    """Build a Chromium proxy config from the account's bound proxy, or None.

    Chromium can't authenticate to SOCKS5 (hard browser limit), so an authed
    SOCKS5 upstream is routed through a local no-auth SOCKS5 bridge (PySocks adds
    the credentials upstream). HTTP proxies and auth-less SOCKS5 pass straight
    through."""
    if not proxy_id:
        return None
    p = db.get_proxy(proxy_id, decrypt=True)
    if not p:
        return None
    scheme = (p.get("scheme") or "http").lower()
    host, port = p["host"], p["port"]
    user, pw = p.get("username", ""), p.get("password", "")
    if scheme in ("socks5", "socks5h") and user:
        from server import socksbridge
        lp = socksbridge.local_port_for(host, port, user, pw)
        return {"server": f"socks5://127.0.0.1:{lp}"}
    cfg = {"server": f"{scheme}://{host}:{port}"}
    if user:
        cfg["username"] = user
        cfg["password"] = pw
    return cfg


def _run_one(account_id, action_type, trigger, sa_id):
    settings = db.get_settings(decrypt=True)
    acc = db.get_account(account_id, decrypt_cookies=True)
    if not acc or not acc.get("active") or not acc.get("auth_token") or not acc.get("ct0"):
        if sa_id:
            db.mark_scheduled(sa_id, "skipped")
        return
    account = {
        "id": acc["id"], "name": acc["name"], "username": acc.get("username", ""),
        "auth_token": acc["auth_token"], "ct0": acc["ct0"],
        "keywords": acc.get("keywords", []), "mode": acc.get("mode", "search"),
        "dry_run": settings.get("dry_run", "1") == "1",
        "headless": settings.get("headless", "1") == "1",
        "slow_mo_ms": 0,
    }
    proxy = _resolve_proxy(acc.get("proxy_id"))
    if proxy:
        account["proxy"] = proxy
        log.info("account %s -> routing via proxy %s", acc["name"], proxy["server"])
    _current.update(account_id=account_id, name=acc["name"], action=action_type, trigger=trigger)
    started = datetime.now(db.TZ).isoformat(timespec="seconds")
    run_status, counts, run_log = "ok", {}, None
    try:
        result = execute_action(account, action_type, settings, log_dir=f"logs/{account_id}")
        counts = result.get("counts", {})
        run_log = result.get("run_log")
        if result.get("status") == "error" or not result.get("logged_in"):
            run_status = "error"
    except Exception as e:
        run_status = "error"
        counts = {"error": str(e)}
        log.exception("execute_action failed for account %s", account_id)

    # record run, then mark the just-run entry done BEFORE scheduling next —
    # otherwise the still-'running' row blocks insert_next_if_idle and the next
    # tick kicks off a near-now action (actions would fire every ~1 min).
    rid = db.record_run(account_id, started, datetime.now(db.TZ).isoformat(timespec="seconds"),
                        run_status, counts, run_log, trigger, result.get("exit_ip", ""))
    if sa_id:
        db.mark_scheduled(sa_id, "done" if run_status == "ok" else "error", rid)
    acc_fresh = db.get_account(account_id)  # latest quotas
    if acc_fresh and acc_fresh.get("active"):
        schedule_next(acc_fresh)
    _current.update(account_id=None, name=None, action=None, trigger=None)
    log.info("action %s #%s done (%s): %s", action_type, account_id, trigger, counts)
    return rid

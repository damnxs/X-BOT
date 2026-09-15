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
from xbot.runner import execute_action, execute_autoreply, execute_raid

log = logging.getLogger("xbot.scheduler")

POLL_MINUTES = 1  # internal cadence; not a user setting

_queue = deque()           # warm-up actions: (account_id, action_type, trigger, sa_id)
_ar_queue = deque()        # auto-reply cycles — own worker so one long cycle
                           # can't starve warm-up actions (or vice versa)
_lock = threading.Lock()
_scheduler = None
_worker_started = False
_current = {"account_id": None, "name": None, "action": None, "trigger": None}
_ar_current = {"account_id": None, "name": None, "action": None, "trigger": None}


def ar_current():
    """What the auto-reply worker is doing right now (None when idle). Kept
    separate from _current so the warm-up status bar never shows autoreply."""
    return dict(_ar_current) if _ar_current["account_id"] else None


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
        threading.Thread(target=_ar_worker_loop, daemon=True, name="xbot-autoreply").start()
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
        ({"autoreply": _ar_queue}.get(action_type, _queue)).append(
            (account_id, action_type, trigger, sa_id))


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
    # Twitter Auto Reply runs on its own per-account statuses, independent of
    # the warm-up schedule toggle.
    _autoreply_tick()
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


def _ar_worker_loop():
    """Dedicated worker for auto-reply cycles (search + N replies can run 10+
    minutes); keeps warm-up actions flowing on the main worker."""
    while True:
        item = None
        with _lock:
            if _ar_queue:
                item = _ar_queue.popleft()
        if item is None:
            time.sleep(2)
            continue
        account_id, _, trigger, _ = item
        try:
            _run_autoreply(account_id, trigger)
        except Exception:
            log.exception("autoreply worker error on account %s", account_id)


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
        "pop_min_likes": int(acc.get("pop_min_likes") or 0),
        "pop_min_replies": int(acc.get("pop_min_replies") or 0),
        "pop_tab": acc.get("pop_tab") or "live",
        "dry_run": settings.get("dry_run", "1") == "1",
        "headless": settings.get("headless", "1") == "1",
        "slow_mo_ms": 0,
    }
    proxy = _resolve_proxy(acc.get("proxy_id"))
    if proxy:
        account["proxy"] = proxy
        log.info("account %s -> routing via proxy %s", acc["name"], proxy["server"])
    else:
        log.info("account %s -> using local IP (no proxy)", acc["name"])
    _current.update(account_id=account_id, name=acc["name"], action=action_type, trigger=trigger)
    started = datetime.now(db.TZ).isoformat(timespec="seconds")
    rid = db.start_run(account_id, started, trigger, action_type)  # 'running' -> live "In Progress"
    account["run_id"] = rid
    # runner stamps delay_until when the pre-action pause begins -> live countdown
    account["on_delay"] = lambda secs: db.set_run_delay(
        rid, (datetime.now(db.TZ) + timedelta(seconds=secs)).isoformat(timespec="seconds"))
    run_status, counts, run_log, exit_ip = "ok", {}, None, ""
    try:
        result = execute_action(account, action_type, settings, log_dir=f"logs/{account_id}")
        counts = result.get("counts", {})
        run_log = result.get("run_log")
        exit_ip = result.get("exit_ip", "")
        if result.get("status") == "error" or not result.get("logged_in"):
            run_status = "error"
            if not counts.get("error"):
                counts["error"] = "login failed" if not result.get("logged_in") else "run failed"
    except Exception as e:
        run_status = "error"
        counts = {"error": str(e)}
        log.exception("execute_action failed for account %s", account_id)
    finally:
        # complete the run row. Done BEFORE schedule_next: a still-'running'
        # scheduled row would block insert_next_if_idle (next tick would fire ~now).
        if rid:
            db.finish_run(rid, datetime.now(db.TZ).isoformat(timespec="seconds"),
                          run_status, counts, run_log, exit_ip)

    if sa_id:
        db.mark_scheduled(sa_id, "done" if run_status == "ok" else "error", rid)
    acc_fresh = db.get_account(account_id)  # latest quotas
    if acc_fresh and acc_fresh.get("active"):
        schedule_next(acc_fresh)
    _current.update(account_id=None, name=None, action=None, trigger=None)
    log.info("action %s #%s done (%s): %s", action_type, account_id, trigger, counts)
    return rid


def run_raid(account_id, tweet_id, action, reply_text="", raid_id=None):
    """Run a single raid action (like/retweet/reply) on a specific tweet for one
    account. Runs synchronously in the caller's thread (the HTTP endpoint). Records
    a run with trigger='raid' so it shows in the Activity feed + logs. If raid_id
    is given (mass raid step) the run is linked to that raid; otherwise a single
    raid row is created and finished here."""
    settings = db.get_settings(decrypt=True)
    acc = db.get_account(account_id, decrypt_cookies=True)
    if not acc or not acc.get("auth_token") or not acc.get("ct0"):
        return {"status": "error", "message": "account has no saved cookies", "exit_ip": "", "run_id": None}
    account = {
        "id": acc["id"], "name": acc["name"], "username": acc.get("username", ""),
        "auth_token": acc["auth_token"], "ct0": acc["ct0"],
        "dry_run": settings.get("dry_run", "1") == "1",
        "headless": settings.get("headless", "1") == "1",
        "slow_mo_ms": 0,
    }
    proxy = _resolve_proxy(acc.get("proxy_id"))
    if proxy:
        account["proxy"] = proxy
        log.info("raid account %s -> routing via proxy %s", acc["name"], proxy["server"])
    else:
        log.info("raid account %s -> using local IP (no proxy)", acc["name"])
    _current.update(account_id=account_id, name=acc["name"], action=action, trigger="raid")
    started = datetime.now(db.TZ).isoformat(timespec="seconds")
    rid = db.start_run(account_id, started, "raid", action, tweet_id)
    is_single = raid_id is None
    if is_single:
        raid_id = db.start_raid(tweet_id, "single", {action: 1})
    db.set_run_raid(rid, raid_id)
    account["on_delay"] = lambda secs: db.set_run_delay(
        rid, (datetime.now(db.TZ) + timedelta(seconds=secs)).isoformat(timespec="seconds"))
    run_status, counts, run_log, exit_ip = "ok", {}, None, ""
    try:
        result = execute_raid(account, tweet_id, action, reply_text, settings, log_dir=f"logs/{account_id}")
        counts = result.get("counts", {})
        run_log = result.get("run_log")
        exit_ip = result.get("exit_ip", "")
        if result.get("status") == "error" or not result.get("logged_in"):
            run_status = "error"
            if not counts.get("error"):
                counts["error"] = "login failed" if not result.get("logged_in") else "raid failed"
    except Exception as e:
        run_status = "error"
        counts = {"error": str(e)}
        log.exception("raid failed for account %s", account_id)
    finally:
        if rid:
            db.finish_run(rid, datetime.now(db.TZ).isoformat(timespec="seconds"),
                          run_status, counts, run_log, exit_ip)
    db.bump_raid(raid_id, action, run_status == "ok")
    if is_single:
        db.finish_raid(raid_id, "completed" if run_status == "ok" else "failed")
    _current.update(account_id=None, name=None, action=None, trigger=None)
    log.info("raid %s #%s done: %s", action, account_id, counts)
    return {"status": run_status, "message": counts.get("error", ""), "exit_ip": exit_ip, "run_id": rid}


# ---- mass raid: automatic, balanced account selection ------------------

# window of top-ranked accounts to random-pick from (human-like rotation)
_MASS_WINDOW = 8


def _score_account(last_ts):
    """Higher score = should be picked sooner. Currently driven by recency (older
    last action = higher). ponytail: add weights here later — proxy, geo, daily
    quota, success rate, premium tier — without changing the callers."""
    if not last_ts:
        return float("inf")  # never used this action -> top priority
    try:
        return -datetime.fromisoformat(last_ts).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _pick_ranked(ranked, count):
    """Sliding-window random pick over accounts already sorted best-first. Prefers
    the top-ranked but shuffles within a window so the same accounts aren't always
    first (more human-like than always picking #1)."""
    pool = list(ranked)
    picked, window = [], max(1, min(len(pool), _MASS_WINDOW))
    while pool and len(picked) < count:
        choice = random.choice(pool[:window])
        pool.remove(choice)
        picked.append(choice)
    return picked


def mass_plan(tweet_id, likes, retweets, replies):
    """Choose which accounts perform each action on tweet_id, balancing load.

    Eligibility per action: active, not currently busy, and hasn't already done
    this action on this tweet. Ranked oldest-last-action-first, then random-picked
    from the top window. Returns the plan + eligibility + any shortfalls."""
    accounts = db.list_accounts(active_only=True)
    busy = db.running_account_ids()
    last_by = db.last_action_ts_by_type()

    requested = {"like": int(likes or 0), "retweet": int(retweets or 0), "reply": int(replies or 0)}
    eligible = {}
    errors = []
    plan = []

    for action, count in requested.items():
        if count <= 0:
            eligible[action] = 0
            continue
        done = db.accounts_that_did_action(tweet_id, action)
        cand = []
        for a in accounts:
            if a["id"] in busy or a["id"] in done:
                continue
            cand.append({"id": a["id"], "name": a["name"], "username": a.get("username", ""),
                         "last_ts": last_by.get((a["id"], action), "")})
        eligible[action] = len(cand)
        if len(cand) < count:
            errors.append({"action": action, "requested": count, "eligible": len(cand)})
        cand.sort(key=lambda x: _score_account(x["last_ts"]), reverse=True)  # best first
        for a in _pick_ranked(cand, min(count, len(cand))):
            plan.append({"account_id": a["id"], "name": a["name"], "username": a["username"], "action": action})

    return {"requested": requested, "eligible": eligible, "errors": errors, "plan": plan}


# ---- twitter auto reply: independent per-account cycles ----------------

def _queued_account_ids():
    with _lock:
        ids = {a for a, _, _, _ in _queue}
        ids.update(a for a, _, _, _ in _ar_queue)
        return ids


def _ar_interval(cfg):
    """Search interval in minutes, clamped to >= 1 — a stale/typed 0 or negative
    value would otherwise fire the tick every minute."""
    try:
        return max(1, int(float(cfg.get("interval_minutes") or 10)))
    except (TypeError, ValueError):
        return 10


def enqueue_autoreply(ar, trigger):
    """Queue one auto-reply cycle and push the account's next run forward first,
    so the minute tick can't double-queue while the cycle is in flight."""
    nxt = datetime.now(db.TZ) + timedelta(minutes=_ar_interval(ar["config"]))
    db.ar_set_next_run(ar["id"], nxt.isoformat(timespec="seconds"))
    enqueue(ar["account_id"], "autoreply", trigger)
    db.ar_log_add(ar["id"], ar["account_id"],
                  f"cycle queued ({trigger}) — next check {nxt.strftime('%H:%M')}")


def autoreply_run_now(ar_id):
    """Manual 'run now' from the API. Returns (ok, message)."""
    ar = db.ar_get(ar_id)
    if not ar:
        return False, "automation row not found"
    if ar["account_id"] in _queued_account_ids() | db.running_account_ids():
        return False, "account is busy — cycle already queued or running"
    enqueue_autoreply(ar, "manual")
    return True, "cycle queued"


def _autoreply_tick():
    """Every tick: queue a cycle for each active automation whose interval
    elapsed. The ONLY reply cap is the account's tweets_per_cycle (enforced
    inside the cycle). One account erroring never touches the others."""
    now = datetime.now(db.TZ)
    now_iso = now.isoformat(timespec="seconds")
    busy = _queued_account_ids() | db.running_account_ids()
    for ar in db.ar_list():
        try:
            if ar["status"] != "active":
                continue
            nxt = ar["next_run_at"]
            if not nxt or nxt > now_iso:
                continue
            cfg = ar["config"]
            interval = timedelta(minutes=_ar_interval(cfg))
            if not cfg.get("keywords"):
                # no keywords -> the query would be operators only (all tweets).
                # Keep checking back in case keywords get configured later.
                db.ar_set_next_run(ar["id"], (now + interval).isoformat(timespec="seconds"))
                continue
            if ar["account_id"] in busy:
                db.ar_set_next_run(ar["id"], (now + interval).isoformat(timespec="seconds"))
                continue
            enqueue_autoreply(ar, "schedule")
        except Exception:
            # a corrupt config row must never abort the tick for the other
            # accounts (or the warm-up scheduling that runs after it)
            log.exception("autoreply tick failed for account %s", ar.get("account_id"))


def _run_autoreply(account_id, trigger):
    """Worker entry for one auto-reply cycle: records the run, executes the
    search->filter->AI->reply pipeline, persists history + activity log, and
    schedules the next cycle. A login failure only pauses this account."""
    settings = db.get_settings(decrypt=True)
    acc = db.get_account(account_id, decrypt_cookies=True)
    ar = db.ar_get_by_account(account_id)
    if not (acc and ar):
        log.error("autoreply: account %s missing or unassigned", account_id)
        return
    ar_id, cfg = ar["id"], ar["config"]
    if not (acc.get("auth_token") and acc.get("ct0")):
        db.ar_update(ar_id, {"status": "error"})
        db.ar_log_add(ar_id, account_id, "no saved cookies — automation set to error", "error")
        return
    if trigger != "manual" and ar["status"] != "active":   # paused/stopped while queued
        db.ar_log_add(ar_id, account_id, f"cycle dropped: automation is {ar['status']}")
        return

    now = datetime.now(db.TZ)
    max_replies = max(0, int(cfg.get("tweets_per_cycle") or 0))  # the only cap

    _ar_current.update(account_id=account_id, name=acc["name"], action="autoreply", trigger=trigger)
    started = now.isoformat(timespec="seconds")
    rid = db.start_run(account_id, started, trigger, "autoreply")
    db.ar_log_add(ar_id, account_id, f"cycle start ({trigger})")

    if max_replies == 0:
        msg = "tweets per cycle is 0 — cycle skipped"
        db.ar_log_add(ar_id, account_id, msg)
        db.finish_run(rid, datetime.now(db.TZ).isoformat(timespec="seconds"),
                      "skipped", {"note": msg}, None)
    else:
        account = {
            "id": acc["id"], "name": acc["name"], "username": acc.get("username", ""),
            "auth_token": acc["auth_token"], "ct0": acc["ct0"],
            "dry_run": settings.get("dry_run", "1") == "1",
            "headless": settings.get("headless", "1") == "1",
            "slow_mo_ms": 0,
            "on_delay": lambda secs: db.set_run_delay(
                rid, (datetime.now(db.TZ) + timedelta(seconds=secs)).isoformat(timespec="seconds")),
            # step-by-step live feed: runner emits each pipeline step as it happens
            "on_event": lambda msg, level="info": db.ar_log_add(ar_id, account_id, msg, level),
        }
        proxy = _resolve_proxy(acc.get("proxy_id"))
        if proxy:
            account["proxy"] = proxy
        run_status, counts, run_log, exit_ip = "ok", {}, None, ""
        try:
            result = execute_autoreply(account, cfg, db.ar_processed_ids(ar_id),
                                       max_replies, settings, log_dir=f"logs/{account_id}")
            counts = result.get("counts", {})
            run_log = result.get("run_log")
            exit_ip = result.get("exit_ip", "")
            # unassigned while the cycle ran? drop the results — nothing to
            # attach them to (ar_tweets/ar_log carry no FK)
            if not db.ar_get(ar_id):
                log.info("autoreply %s unassigned mid-cycle — discarding results", ar_id)
            else:
                # per-tweet steps were already streamed live by the runner's
                # on_event callback — only persist the tweet rows here
                db.ar_record_tweets(ar_id, account_id, result.get("found") or [])
                if not result.get("logged_in") or result.get("status") == "error":
                    run_status = "error"
                    if not counts.get("error"):
                        counts["error"] = "login failed" if not result.get("logged_in") else "cycle failed"
                    db.ar_update(ar_id, {"status": "error"})
                    db.ar_log_add(ar_id, account_id,
                                  f"cycle error: {counts.get('error')} — automation set to error", "error")
                else:
                    db.ar_log_add(ar_id, account_id,
                                  "cycle done: found {found}, qualified {qualified}, "
                                  "replied {reply}, failed {reply_fail}".format(**{
                                      **{"found": 0, "qualified": 0, "reply": 0, "reply_fail": 0}, **counts}))
        except Exception as e:
            run_status = "error"
            counts = {"error": str(e)}
            db.ar_update(ar_id, {"status": "error"})
            db.ar_log_add(ar_id, account_id, f"cycle crashed: {e}", "error")
            log.exception("autoreply cycle failed for account %s", account_id)
        finally:
            if rid:
                db.finish_run(rid, datetime.now(db.TZ).isoformat(timespec="seconds"),
                              run_status, counts, run_log, exit_ip)

    if db.ar_get(ar_id):
        # next cycle: interval from completion, ±20% jitter; recover from 'error' too
        interval = _ar_interval(cfg) * random.uniform(0.8, 1.2)
        db.ar_set_next_run(ar_id, (datetime.now(db.TZ) + timedelta(minutes=interval)).isoformat(timespec="seconds"))
        db.ar_set_last_run(ar_id, datetime.now(db.TZ).isoformat(timespec="seconds"))
    _ar_current.update(account_id=None, name=None, action=None, trigger=None)
    return rid

"""Run one account cycle end-to-end. Shared by the CLI (bot.py) and the server.

execute_run(account, settings, log_dir) is the single entry point: it sets up
per-account logging, launches Playwright, logs in with the account's (plaintext)
cookies, runs the post loop (gated on a global OpenAI key) + the like/retweet
split loop, and returns counts. No config.yaml or argparse here — callers build
the `account` dict.
"""
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

from xbot.llm import LLM
from xbot.log import ActionLog, setup_logging
from xbot.session import XSession

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_SETTINGS = {
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_system_prompt": "You write short, friendly, on-topic X/Twitter messages. No quote marks.",
    "min_delay_seconds": 30,
    "max_delay_seconds": 90,
}


def _jitter(settings):
    lo = settings.get("min_delay_seconds", 30)
    hi = settings.get("max_delay_seconds", 90)
    if hi <= 0:
        return
    time.sleep(random.uniform(lo, hi))


def _load_seen(log_dir):
    try:
        with open(os.path.join(log_dir, "seen.json")) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_seen(log_dir, seen):
    with open(os.path.join(log_dir, "seen.json"), "w") as f:
        json.dump(sorted(seen), f)


def _gather_candidates(session, mode, keywords, log, target):
    candidates = []
    if mode == "timeline":
        candidates = session.timeline(limit=target + 4).get("results", [])
    else:
        per_kw = max(6, target + 2)
        for kw in keywords:
            candidates.extend(session.search(kw, limit=per_kw).get("results", []))
    deduped, seen = [], set()
    for t in candidates:
        if t["url"] not in seen:
            seen.add(t["url"])
            deduped.append(t)
    log.info("gathered %d unique candidates", len(deduped))
    return deduped


def execute_run(account, settings=None, log_dir="logs"):
    """Run one cycle for an account. Returns {logged_in, counts, run_log}.

    account: per the contract (plaintext cookies, limits, keywords, mode, ...).
    settings: global settings (openai key/model/prompt, delays). Defaults merged.
    log_dir: where this run's logs/screenshots/seen live (e.g. logs/<account_id>).
    """
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    # settings may arrive from SQLite as strings (TEXT); coerce numerics here so
    # _jitter's `hi <= 0` never hits a str-vs-int TypeError.
    for _k in ("min_delay_seconds", "max_delay_seconds"):
        if str(settings.get(_k, "")).strip() not in ("", "None"):
            settings[_k] = int(float(settings[_k]))
    os.makedirs(log_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log = setup_logging(run_id, log_dir, "DEBUG")
    log = logging.getLogger("xbot")

    screenshots_dir = os.path.join(log_dir, "screenshots")
    action_log = ActionLog(run_id, os.path.join(log_dir, "actions.jsonl"), screenshots_dir, True)

    limits = account.get("limits", {})
    max_posts = int(limits.get("max_posts", 0))
    max_likes = int(limits.get("max_likes", 0))
    max_retweets = int(limits.get("max_retweets", 0))
    like_p = float(account.get("like_probability", 0.6))
    rt_p = float(account.get("retweet_probability", 0.4))
    mode = account.get("mode", "search")
    keywords = account.get("keywords", []) or []
    dry_run = bool(account.get("dry_run", True))
    headless = account.get("headless", True)
    slow_mo = account.get("slow_mo_ms", 0)

    log.info("=" * 60)
    log.info("run %s | account=%s mode=%s dry_run=%s", run_id, account.get("name"), mode, dry_run)

    llm = None
    if settings.get("openai_api_key"):
        llm = LLM({
            "api_key": settings["openai_api_key"],
            "model": settings.get("openai_model", "gpt-4o-mini"),
            "system_prompt": settings.get("openai_system_prompt"),
        })
        log.info("OpenAI key present — posting available")
    else:
        log.info("no OpenAI key — like/retweet only")

    seen = _load_seen(log_dir)
    counts = {"post": 0, "post_fail": 0, "like": 0, "like_fail": 0,
              "retweet": 0, "retweet_fail": 0}
    logged_in = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        session = XSession(page, action_log, account.get("username", ""), dry_run)
        try:
            logged_in = bool(session.login(account["auth_token"], account["ct0"]).get("logged_in"))
            if not logged_in:
                log.error("login failed — aborting. See actions.jsonl + screenshots/.")
            else:
                _run_posts(session, llm, action_log, counts, max_posts, keywords, settings, log)
                _run_engage(session, counts, seen, max_likes, max_retweets, like_p, rt_p,
                            mode, keywords, settings, log)
        finally:
            browser.close()

    _save_seen(log_dir, seen)
    summary = {"action": "summary", "mode": mode, "dry_run": dry_run,
               "counts": counts, "seen_total": len(seen)}
    action_log.record(**summary)
    log.info("summary: %s", json.dumps(summary))
    return {"logged_in": logged_in, "counts": counts, "run_log": run_log}


def _run_posts(session, llm, action_log, counts, max_posts, keywords, settings, log):
    if not (llm and max_posts):
        if max_posts and not llm:
            log.info("max_posts>0 but no OpenAI key — skipping posts")
        return
    for i in range(max_posts):
        topic = random.choice(keywords or [None])
        try:
            gen = llm.generate_post(topic)
        except Exception as e:
            log.error("LLM post generation failed: %s", e)
            action_log.record(action="llm_post", status="error", error={"message": str(e)})
            continue
        res = session.post(gen["output"])
        res["llm"] = gen
        if res["status"] == "ok":
            counts["post"] += 1
        elif res["status"] == "error":
            counts["post_fail"] += 1
        if i < max_posts - 1:
            _jitter(settings)


def _run_engage(session, counts, seen, max_likes, max_retweets, like_p, rt_p,
                mode, keywords, settings, log):
    candidates = _gather_candidates(session, mode, keywords, log, max_likes + max_retweets)
    random.shuffle(candidates)
    for tweet in candidates:
        like_open = counts["like"] < max_likes
        rt_open = counts["retweet"] < max_retweets
        if not (like_open or rt_open):
            break
        if tweet["url"] in seen:
            continue
        if like_open and rt_open:
            do_like = random.random() < (like_p / (like_p + rt_p))
        else:
            do_like = like_open
        res = session.like(tweet) if do_like else session.retweet(tweet)
        if res["status"] == "ok":
            seen.add(tweet["url"])
            counts["like" if do_like else "retweet"] += 1
        elif res["status"] == "error":
            counts["like_fail" if do_like else "retweet_fail"] += 1
        if counts["like"] < max_likes or counts["retweet"] < max_retweets:
            _jitter(settings)


def _first_candidate(session, mode, keywords, seen, log):
    """First tweet from the feed that hasn't been engaged yet."""
    for c in _gather_candidates(session, mode, keywords, log, 10):
        if c["url"] not in seen:
            return c
    return None


def _first_reply_candidate(session, mode, keywords, seen, log, max_age_hours, min_likes):
    """Most-popular tweet that is recent (<= max_age_hours old) and popular
    (>= min_likes).

    Searches the 'Latest' (live) tab so the recency filter actually has recent
    tweets — the 'Top' tab surfaces popular tweets that are usually days old and
    would all fail a <=1h window. We then keep only those within the age window
    and >= min_likes, and pick the most-liked. Returns None if none qualify.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    eligible = []
    for kw in (keywords or [None]):
        res = session.timeline(limit=20) if mode == "timeline" else session.search(kw, limit=20, tab="live")
        for c in res.get("results", []):
            if c["url"] in seen:
                continue
            t = c.get("time")
            if not t:
                continue
            try:
                posted = datetime.fromisoformat(t.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if posted.tzinfo is None:
                posted = posted.replace(tzinfo=timezone.utc)
            if posted < cutoff or posted > now + timedelta(minutes=5):
                continue  # not within the recency window
            if int(c.get("likes", 0)) < min_likes:
                continue
            eligible.append(c)
    if not eligible:
        return None
    eligible.sort(key=lambda c: int(c.get("likes", 0)), reverse=True)
    log.info("reply: %d eligible (top likes=%d)", len(eligible), eligible[0].get("likes"))
    return eligible[0]


def execute_action(account, action_type, settings=None, log_dir="logs"):
    """Run ONE action (post|like|retweet|reply) for an account.

    Used by the scheduler: each entry in an account's randomized daily plan is
    a single action executed in its own short browser session. No inter-action
    jitter here — the randomized schedule already spaces actions apart.
    Returns {logged_in, status, counts, run_log}.
    """
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    for _k in ("min_delay_seconds", "max_delay_seconds"):
        if str(settings.get(_k, "")).strip() not in ("", "None"):
            settings[_k] = int(float(settings[_k]))

    os.makedirs(log_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log = setup_logging(run_id, log_dir, "DEBUG")
    log = logging.getLogger("xbot")
    screenshots_dir = os.path.join(log_dir, "screenshots")
    action_log = ActionLog(run_id, os.path.join(log_dir, "actions.jsonl"), screenshots_dir, True)

    mode = account.get("mode", "search")
    keywords = account.get("keywords", []) or []
    dry_run = bool(account.get("dry_run", True))
    headless = account.get("headless", True)
    slow_mo = account.get("slow_mo_ms", 0)
    log.info("=" * 60)
    log.info("action %s | account=%s dry_run=%s", action_type, account.get("name"), dry_run)

    llm = LLM({
        "api_key": settings["openai_api_key"],
        "model": settings.get("openai_model", "gpt-4o-mini"),
        "system_prompt": settings.get("openai_system_prompt"),
    }) if settings.get("openai_api_key") else None

    counts = {}
    logged_in = False
    status = "ok"  # ok | skipped | error

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT, viewport={"width": 1280, "height": 900}
        )
        page = context.new_page()
        session = XSession(page, action_log, account.get("username", ""), dry_run)
        try:
            logged_in = bool(session.login(account["auth_token"], account["ct0"]).get("logged_in"))
            if not logged_in:
                log.error("login failed — aborting")
                status = "error"
            elif action_type == "post":
                if not llm:
                    log.info("post action but no OpenAI key — skipping")
                    status = "skipped"
                else:
                    topic = random.choice(keywords or [None])
                    try:
                        gen = llm.generate_post(topic)
                    except Exception as e:
                        log.error("LLM post gen failed: %s", e)
                        action_log.record(action="llm_post", status="error", error={"message": str(e)})
                        status = "error"
                    else:
                        res = session.post(gen["output"])
                        res["llm"] = gen
                        if res["status"] == "ok":
                            counts["post"] = 1
                        elif res["status"] == "error":
                            status = "error"
                        else:
                            status = "skipped"
            elif action_type in ("like", "retweet"):
                seen = _load_seen(log_dir)
                cand = _first_candidate(session, mode, keywords, seen, log)
                if not cand:
                    log.info("no candidate for %s — skipping", action_type)
                    status = "skipped"
                else:
                    res = session.like(cand) if action_type == "like" else session.retweet(cand)
                    if res["status"] == "ok":
                        seen.add(cand["url"])
                        counts[action_type] = 1
                    elif res["status"] == "error":
                        status = "error"
                    else:
                        status = "skipped"
                _save_seen(log_dir, seen)
            elif action_type == "reply":
                seen = _load_seen(log_dir)
                if not llm:
                    log.info("reply action but no OpenAI key — skipping")
                    status = "skipped"
                else:
                    max_age = float(settings.get("reply_max_age_hours", 1) or 1)
                    min_likes = int(float(settings.get("reply_min_likes", 50) or 0))
                    cand = _first_reply_candidate(session, mode, keywords, seen, log, max_age, min_likes)
                    if not cand:
                        log.info("no popular+recent (<= %sh, >= %s likes) candidate — skipping", max_age, min_likes)
                        status = "skipped"
                    else:
                        try:
                            gen = llm.generate_reply(cand["text"])
                        except Exception as e:
                            log.error("LLM reply gen failed: %s", e)
                            action_log.record(action="llm_reply", status="error", target=cand, error={"message": str(e)})
                            status = "error"
                        else:
                            res = session.reply(cand, gen["output"])
                            res["llm"] = gen
                            if res["status"] == "ok":
                                seen.add(cand["url"])
                                counts["reply"] = 1
                            elif res["status"] == "error":
                                status = "error"
                            else:
                                status = "skipped"
                _save_seen(log_dir, seen)
            else:
                log.error("unknown action_type %s", action_type)
                status = "error"
        finally:
            browser.close()

    summary = {"action": "action_summary", "action_type": action_type,
               "dry_run": dry_run, "counts": counts, "status": status}
    action_log.record(**summary)
    log.info("action %s done: %s", action_type, summary)
    return {"logged_in": logged_in, "status": status, "counts": counts, "run_log": run_log}

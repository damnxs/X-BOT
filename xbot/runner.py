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
from xbot.dryrun import DryRunLogger

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Chromium refuses to start as root with the sandbox on (systemd service runs as root)
LAUNCH_ARGS = ["--no-sandbox"] if os.geteuid() == 0 else []

def _browser_context(browser, account):
    """Playwright context. The proxy is applied at browser LAUNCH (chromium.launch
    proxy=) — that's where Chromium reliably honors SOCKS5; setting it on the
    context is unreliable for SOCKS and can silently fall back to direct."""
    ctx_kwargs = dict(user_agent=DEFAULT_USER_AGENT, viewport={"width": 1280, "height": 900})
    return browser.new_context(**ctx_kwargs)


_IP_ECHOES = ("https://api.ipify.org", "https://ifconfig.me/ip", "http://api.ipify.org")


def _capture_exit_ip(page):
    """Exit IP this browser session egresses through (via its proxy). Best-effort:
    never blocks the run on failure. Only called when a proxy is set, so the
    server's real IP is never exposed for direct (proxy-less) runs.

    The action itself always runs through the proxy — this is just the read-back
    used to DISPLAY the exit IP. A single echo/attempt sometimes times out through
    a slow proxy and would blank the IP, so we try a few echoes with retries and
    return the first valid one."""
    for url in _IP_ECHOES:
        for _ in range(2):
            try:
                resp = page.goto(url, timeout=8000, wait_until="domcontentloaded")
                body = (resp.text() if resp else "").strip()
                ip = body if body and len(body) < 45 and any(c.isdigit() for c in body) else ""
                if ip:
                    return ip
            except Exception:
                continue
    return ""


DEFAULT_SETTINGS = {
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "openai_post_system_prompt": "",
    "openai_reply_system_prompt": "",
    "min_delay_seconds": 30,
    "max_delay_seconds": 90,
}


def _jitter(settings):
    lo = settings.get("min_delay_seconds", 30)
    hi = settings.get("max_delay_seconds", 90)
    if hi <= 0:
        return
    time.sleep(random.uniform(lo, hi))


def _human_delay(log, action_type, on_delay=None):
    """Random 3–70s pause after a tweet is selected, before the like/retweet/reply —
    keeps engagement looking human. If on_delay(secs) is given, it records when the
    pause ends so the activity feed can show a live countdown. Logged so the gap in
    the run log is explainable."""
    secs = random.uniform(3, 70)
    if on_delay:
        try:
            on_delay(secs)
        except Exception:
            pass  # countdown is best-effort; never block the action on it
    log.info("waiting %.0fs before %s", secs, action_type)
    time.sleep(secs)


def _load_seen(log_dir, name="seen.json"):
    try:
        with open(os.path.join(log_dir, name)) as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_seen(log_dir, seen, name="seen.json"):
    with open(os.path.join(log_dir, name), "w") as f:
        json.dump(sorted(seen), f)


def _pop_query(keyword, min_likes, min_replies):
    """X advanced-search operators for the popularity mode. min_faves (likes) and
    min_replies filter server-side; which tab (Top/Latest) is chosen by the account's
    pop_tab — Latest surfaces recent popular tweets, Top surfaces all-time popular."""
    q = keyword
    if min_likes:
        q += f" min_faves:{int(min_likes)}"
    if min_replies:
        q += f" min_replies:{int(min_replies)}"
    return q


def _gather_candidates(session, mode, keywords, log, target, pop_min_likes=0, pop_min_replies=0, pop_tab="live"):
    candidates = []
    if mode == "timeline":
        candidates = session.timeline(limit=target + 4).get("results", [])
    else:
        per_kw = max(6, target + 2)
        # popularity mode uses the account's chosen tab (Top or Latest); plain search is Latest
        tab = (pop_tab or "live") if mode == "search_popularity" else "live"
        for kw in keywords:
            q = _pop_query(kw, pop_min_likes, pop_min_replies) if mode == "search_popularity" else kw
            candidates.extend(session.search(q, limit=per_kw, tab=tab).get("results", []))
        # safety net: X's min_faves operator is occasionally loose, so re-check likes
        # client-side. Replies aren't parsed per-tweet, so min_replies is trusted server-side.
        if mode == "search_popularity" and pop_min_likes:
            candidates = [c for c in candidates if int(c.get("likes", 0)) >= int(pop_min_likes)]
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
    pop_min_likes = int(account.get("pop_min_likes") or 0)
    pop_min_replies = int(account.get("pop_min_replies") or 0)
    pop_tab = account.get("pop_tab") or "live"
    dry_run = bool(account.get("dry_run", True))
    dry_log = DryRunLogger(run_id, str(account.get("id") or account.get("name") or "x").replace(" ", "_")) if dry_run else None
    headless = account.get("headless", True)
    slow_mo = account.get("slow_mo_ms", 0)

    log.info("=" * 60)
    log.info("run %s | account=%s mode=%s dry_run=%s", run_id, account.get("name"), mode, dry_run)

    llm = None
    if settings.get("openai_api_key"):
        llm = LLM({
            "api_key": settings["openai_api_key"],
            "model": settings.get("openai_model", "gpt-4o-mini"),
            "post_system": settings.get("openai_post_system_prompt"),
            "reply_system": settings.get("openai_reply_system_prompt"),
        })
        log.info("OpenAI key present — posting available")
    else:
        log.info("no OpenAI key — like/retweet only")

    seen = _load_seen(log_dir)
    counts = {"post": 0, "post_fail": 0, "like": 0, "like_fail": 0,
              "retweet": 0, "retweet_fail": 0}
    logged_in = False

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo, proxy=account.get("proxy"), args=LAUNCH_ARGS)
        context = _browser_context(browser, account)
        page = context.new_page()
        exit_ip = _capture_exit_ip(page) if account.get("proxy") else ""
        session = XSession(page, action_log, account.get("username", ""), dry_run, dry_log=dry_log)
        try:
            logged_in = bool(session.login(account["auth_token"], account["ct0"]).get("logged_in"))
            if not logged_in:
                log.error("login failed — aborting. See actions.jsonl + screenshots/.")
            else:
                _run_posts(session, llm, action_log, counts, max_posts, keywords, settings, log)
                _run_engage(session, counts, seen, max_likes, max_retweets, like_p, rt_p,
                            mode, keywords, settings, log, pop_min_likes, pop_min_replies, pop_tab)
        finally:
            try:
                context.clear_cookies()
                page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
            except Exception:
                pass
            browser.close()

    _save_seen(log_dir, seen)
    dry_report = dry_log.report() if dry_log else None
    summary = {"action": "summary", "mode": mode, "dry_run": dry_run,
               "counts": counts, "seen_total": len(seen), "exit_ip": exit_ip,
               "dry_run_report": dry_report}
    action_log.record(**summary)
    log.info("summary: %s", json.dumps(summary))
    return {"logged_in": logged_in, "counts": counts, "run_log": run_log,
            "exit_ip": exit_ip, "dry_run_report": dry_report}


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
                mode, keywords, settings, log, pop_min_likes=0, pop_min_replies=0, pop_tab="live"):
    candidates = _gather_candidates(session, mode, keywords, log, max_likes + max_retweets,
                                   pop_min_likes, pop_min_replies, pop_tab)
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


# warm-up follows: after an engagement, follow the AUTHOR of that tweet with a
# randomized 1-in-3 chance — decided fresh for EVERY tweet, so which authors get
# followed is random across the whole stream (≈5–7 of every 20 engaged authors)
# and lands differently for every account. No counting, no waiting: a hit
# follows immediately. An author followed once is never re-followed (per-account
# followed.json + X's own Following state).
FOLLOW_ONE_IN = 3


def _warmup_follows(session, tweet, counts, log, followed):
    """See the comment above. Mutates `followed` so the caller persists it."""
    author = (tweet.get("author") or "").lstrip("@").lower()
    if not author:
        return
    if author in followed:
        log.info("warmup: @%s already followed before — skip", author)
        return
    if random.random() >= 1 / FOLLOW_ONE_IN:
        log.info("warmup: @%s not picked this time (random 1:%d across engaged authors)",
                 author, FOLLOW_ONE_IN)
        return
    log.info("warmup: following @%s (author of %s)", author, tweet.get("url"))
    r = session.follow(author)
    if r.get("note") == "already following":
        followed.add(author)
        log.info("warmup: @%s was already following — noted", author)
        counts["follow"] = 0
    elif r["status"] == "ok" or r.get("note") == "dry_run: would follow":
        followed.add(author)
        counts["follow"] = 1
    else:
        counts["follow"] = 0


def _first_candidate(session, mode, keywords, seen, log, pop_min_likes=0, pop_min_replies=0, pop_tab="live"):
    """First tweet from the feed that hasn't been engaged yet."""
    for c in _gather_candidates(session, mode, keywords, log, 10, pop_min_likes, pop_min_replies, pop_tab):
        if c["url"] not in seen:
            return c
    return None


def _first_reply_candidate(session, mode, keywords, seen, log, min_likes):
    """Most-popular tweet for the keyword on the Latest (live) tab.

    Uses X's `min_faves` operator server-side (e.g. 'solana min_faves:2&f=live')
    so X returns popular tweets directly — a plain live search surfaces the newest
    tweets, which are usually low-engagement and would all fail a client-side likes
    check. Picks the most-liked of what X returns. Returns None if none."""
    eligible = []
    for kw in (keywords or [None]):
        q = _pop_query(kw, min_likes, 0) if min_likes else kw   # append min_faves:N
        res = session.timeline(limit=20) if mode == "timeline" else session.search(q, limit=20, tab="live")
        for c in res.get("results", []):
            if c["url"] in seen:
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
    pop_min_likes = int(account.get("pop_min_likes") or 0)
    pop_min_replies = int(account.get("pop_min_replies") or 0)
    pop_tab = account.get("pop_tab") or "live"
    dry_run = bool(account.get("dry_run", True))
    dry_log = DryRunLogger(run_id, str(account.get("id") or account.get("name") or "x").replace(" ", "_")) if dry_run else None
    headless = account.get("headless", True)
    slow_mo = account.get("slow_mo_ms", 0)
    log.info("=" * 60)
    log.info("action %s | account=%s dry_run=%s", action_type, account.get("name"), dry_run)

    llm = LLM({
        "api_key": settings["openai_api_key"],
        "model": settings.get("openai_model", "gpt-4o-mini"),
        "post_system": settings.get("openai_post_system_prompt"),
        "reply_system": settings.get("openai_reply_system_prompt"),
    }) if settings.get("openai_api_key") else None

    counts = {}
    logged_in = False
    status = "ok"  # ok | skipped | error

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo, proxy=account.get("proxy"), args=LAUNCH_ARGS)
        context = _browser_context(browser, account)
        page = context.new_page()
        exit_ip = _capture_exit_ip(page) if account.get("proxy") else ""
        session = XSession(page, action_log, account.get("username", ""), dry_run, dry_log=dry_log)
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
                followed = _load_seen(log_dir, "followed.json")
                cand = _first_candidate(session, mode, keywords, seen, log, pop_min_likes, pop_min_replies, pop_tab)
                if not cand:
                    log.info("no candidate for %s — skipping", action_type)
                    status = "skipped"
                else:
                    _human_delay(log, action_type, account.get("on_delay"))
                    res = session.like(cand) if action_type == "like" else session.retweet(cand)
                    if res["status"] == "ok":
                        seen.add(cand["url"])
                        counts[action_type] = 1
                    elif res["status"] == "error":
                        status = "error"
                    else:
                        status = "skipped"
                    if res["status"] != "error":
                        _warmup_follows(session, cand, counts, log, followed)
                _save_seen(log_dir, seen)
                _save_seen(log_dir, followed, "followed.json")
            elif action_type == "reply":
                seen = _load_seen(log_dir)
                followed = _load_seen(log_dir, "followed.json")
                if not llm:
                    log.info("reply action but no OpenAI key — skipping")
                    status = "skipped"
                else:
                    min_likes = int(float(settings.get("reply_min_likes", 50) or 0))
                    cand = _first_reply_candidate(session, mode, keywords, seen, log, min_likes)
                    if not cand:
                        log.info("no popular (>= %s likes) candidate — skipping", min_likes)
                        status = "skipped"
                    else:
                        try:
                            gen = llm.generate_reply(cand["text"])
                        except Exception as e:
                            log.error("LLM reply gen failed: %s", e)
                            action_log.record(action="llm_reply", status="error", target=cand, error={"message": str(e)})
                            status = "error"
                        else:
                            _human_delay(log, "reply", account.get("on_delay"))
                            res = session.reply(cand, gen["output"])
                            res["llm"] = gen
                            if res["status"] == "ok":
                                seen.add(cand["url"])
                                counts["reply"] = 1
                            elif res["status"] == "error":
                                status = "error"
                            else:
                                status = "skipped"
                            if res["status"] != "error":
                                _warmup_follows(session, cand, counts, log, followed)
                _save_seen(log_dir, seen)
                _save_seen(log_dir, followed, "followed.json")
            else:
                log.error("unknown action_type %s", action_type)
                status = "error"
        finally:
            try:
                context.clear_cookies()
                page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
            except Exception:
                pass
            browser.close()

    # ponytail: guarantee an error reason in counts so the activity feed always
    # shows what went wrong (login vs the action); the full traceback is in run_log.
    if status == "error" and not counts.get("error"):
        counts["error"] = "login failed" if not logged_in else f"{action_type} failed"

    dry_report = dry_log.report() if dry_log else None
    summary = {"action": "action_summary", "action_type": action_type,
               "dry_run": dry_run, "counts": counts, "status": status, "exit_ip": exit_ip,
               "dry_run_report": dry_report}
    action_log.record(**summary)
    log.info("action %s done: %s", action_type, summary)
    return {"logged_in": logged_in, "status": status, "counts": counts,
            "run_log": run_log, "exit_ip": exit_ip, "dry_run_report": dry_report}


def execute_raid(account, tweet_id, action, reply_text="", settings=None, log_dir="logs"):
    """Run ONE raid action (like|retweet|reply) on a SPECIFIC tweet by id.

    Unlike execute_action, there's no search/gathering — we go straight to the
    tweet's permalink and act on it. Used by the Raider page. Records to the same
    per-account actions.jsonl + run log so it shows up in Activity + logs.
    Returns {logged_in, status, counts, run_log, exit_ip}.
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

    dry_run = bool(account.get("dry_run", True))
    dry_log = DryRunLogger(run_id, str(account.get("id") or account.get("name") or "x").replace(" ", "_")) if dry_run else None
    headless = account.get("headless", True)
    slow_mo = account.get("slow_mo_ms", 0)
    log.info("=" * 60)
    log.info("raid %s | account=%s action=%s tweet=%s dry_run=%s",
             run_id, account.get("name"), action, tweet_id, dry_run)

    tweet = {"url": f"https://x.com/i/status/{tweet_id}", "text": ""}
    counts = {}
    logged_in = False
    status = "ok"  # ok | skipped | error
    exit_ip = ""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo, proxy=account.get("proxy"), args=LAUNCH_ARGS)
        context = _browser_context(browser, account)
        page = context.new_page()
        session = XSession(page, action_log, account.get("username", ""), dry_run, dry_log=dry_log)
        try:
            logged_in = bool(session.login(account["auth_token"], account["ct0"]).get("logged_in"))
            if not logged_in:
                log.error("login failed — raid aborted")
                status = "error"
                counts["error"] = "login failed"
            elif action == "like":
                _human_delay(log, action, account.get("on_delay"))
                res = session.like(tweet)
                if res["status"] == "ok": counts["like"] = 1
                elif res["status"] == "error": status = "error"; counts["error"] = "like failed"
                else: status = "skipped"
            elif action == "retweet":
                _human_delay(log, action, account.get("on_delay"))
                res = session.retweet(tweet)
                if res["status"] == "ok": counts["retweet"] = 1
                elif res["status"] == "error": status = "error"; counts["error"] = "retweet failed"
                else: status = "skipped"
            elif action == "reply":
                _human_delay(log, action, account.get("on_delay"))
                res = session.reply(tweet, reply_text or "")
                if res["status"] == "ok": counts["reply"] = 1
                elif res["status"] == "error": status = "error"; counts["error"] = "reply failed"
                else: status = "skipped"
            else:
                status = "error"
                counts["error"] = f"unknown action: {action}"
            if account.get("proxy"):
                exit_ip = _capture_exit_ip(page)
        finally:
            try:
                context.clear_cookies()
                page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
            except Exception:
                pass
            browser.close()

    if status == "error" and not counts.get("error"):
        counts["error"] = "login failed" if not logged_in else f"{action} failed"
    dry_report = dry_log.report() if dry_log else None
    summary = {"action": "raid_summary", "action_type": action, "tweet_id": tweet_id,
               "dry_run": dry_run, "counts": counts, "status": status, "exit_ip": exit_ip,
               "dry_run_report": dry_report}
    action_log.record(**summary)
    log.info("raid %s done: %s", action, summary)
    return {"logged_in": logged_in, "status": status, "counts": counts,
            "run_log": run_log, "exit_ip": exit_ip, "dry_run_report": dry_report}


# ---- twitter auto reply cycle ------------------------------------------

def build_autoreply_query(cfg, now=None):
    """X advanced-search query from an account's auto-reply config: keyword OR
    group, -excluded words, min_faves/min_retweets/min_replies thresholds,
    lang:, and a since: floor for the max tweet age. Server-side operators do
    the coarse filtering; the client-side pass re-checks what X applies loosely."""
    now = now or datetime.now(timezone.utc)
    parts = []
    kws = [k.strip() for k in (cfg.get("keywords") or []) if k and k.strip()]
    if kws:
        parts.append(" OR ".join(f'"{k}"' if " " in k else k for k in kws))
    for ex in (cfg.get("exclude_keywords") or []):
        ex = ex.strip()
        if ex:
            parts.append("-" + (f'"{ex}"' if " " in ex else ex))
    for key, op in (("min_likes", "min_faves"), ("min_retweets", "min_retweets"),
                    ("min_replies", "min_replies")):
        v = int(cfg.get(key) or 0)
        if v > 0:
            parts.append(f"{op}:{v}")
    lang = (cfg.get("lang") or "").strip()
    if lang:
        parts.append(f"lang:{lang}")
    age = int(cfg.get("max_age_hours") or 0)
    if age > 0:
        parts.append("since:" + (now - timedelta(hours=age)).strftime("%Y-%m-%d"))
    return " ".join(parts)


def autoreply_skip_reason(tweet, cfg, processed_ids, now=None):
    """Filter pipeline. Returns None when the tweet is qualified, else the reason
    it was skipped (logged + stored in history)."""
    now = now or datetime.now(timezone.utc)
    text = (tweet.get("text") or "").lower()
    for ex in (cfg.get("exclude_keywords") or []):
        if ex and ex.strip().lower() in text:
            return f"excluded: {ex}"
    max_age = float(cfg.get("max_age_hours") or 0)
    if max_age > 0 and tweet.get("time"):
        try:
            posted = datetime.fromisoformat(tweet["time"].replace("Z", "+00:00"))
            age_h = (now - posted).total_seconds() / 3600
            if age_h > max_age:
                return f"age {age_h:.0f}h > {max_age:.0f}h"
        except ValueError:
            pass
    for key, field in (("min_likes", "likes"), ("min_retweets", "retweets"),
                       ("min_replies", "replies")):
        need = int(cfg.get(key) or 0)
        got = int(tweet.get(field, 0) or 0)
        if need > 0 and got < need:
            return f"{field} {got} < {need}"
    tweet_id = tweet.get("url", "").rstrip("/").split("/")[-1]
    if tweet_id and tweet_id in processed_ids:
        return "already processed"
    return None


def autoreply_reply_error(text, cfg, seen_replies):
    """Reply validation before posting. Returns None when ok, else the reason."""
    text = (text or "").strip()
    if not text:
        return "empty reply"
    if len(text) > int(cfg.get("max_reply_chars") or 280):
        return f"too long ({len(text)} chars)"
    for w in (cfg.get("banned_words") or []):
        if w and w.strip().lower() in text.lower():
            return f"banned word: {w}"
    if text.lower() in seen_replies:
        return "duplicate response"
    return None


def _autoreply_user_prompt(tweet):
    """What the AI sees for each tweet: author, engagement, full text."""
    meta = (f'{tweet.get("likes", 0)} likes · {tweet.get("retweets", 0)} reposts · '
            f'{tweet.get("replies", 0)} replies')
    return f"Tweet by @{tweet.get('author', '?')} ({meta}):\n\n{tweet.get('text', '')}"


def autoreply_resolve_reply(out):
    """Turn one LLM output into (reply_text, skip_reason).

    Plain text -> (text, None). But system prompts can instruct the model to
    answer as a JSON verdict (decision / draft_reply, e.g. a quality-gate
    prompt) — the verdict must be honored, never posted raw: 'skip' or a
    missing draft means skip; otherwise the draft is the reply."""
    if out and out.lstrip().startswith("{"):
        try:
            v = json.loads(out)
        except ValueError:
            return out, None
        if isinstance(v, dict) and "decision" in v:
            draft = str(v.get("draft_reply") or "").strip()
            if draft.upper() in ("N/A", "NA"):
                draft = ""
            if str(v["decision"]).lower() != "skip" and draft:
                return draft, None
            return None, f"llm skip: {str(v.get('reason') or v['decision'])[:120]}"
    return out, None


def execute_autoreply(account, cfg, processed_ids, max_replies, settings=None, log_dir="logs"):
    """One Twitter Auto Reply cycle for one account:

        advanced search -> collect -> filter (keyword/age/engagement/duplicate)
        -> AI reply (the account's own system prompt) -> validate -> post.

    Runs in a single browser session and returns the standard execute_* shape
    plus `found`: one record per tweet seen this cycle with its final status
    (skipped|replied|failed + reason), which the scheduler persists as history.
    `max_replies` is the per-cycle reply budget (hourly/daily limits applied by
    the caller)."""
    settings = {**DEFAULT_SETTINGS, **(settings or {})}
    os.makedirs(log_dir, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log = setup_logging(run_id, log_dir, "DEBUG")
    log = logging.getLogger("xbot")
    action_log = ActionLog(run_id, os.path.join(log_dir, "actions.jsonl"),
                           os.path.join(log_dir, "screenshots"), True)

    dry_run = bool(account.get("dry_run", True))
    dry_log = DryRunLogger(run_id, str(account.get("id") or "x").replace(" ", "_")) if dry_run else None
    log.info("=" * 60)
    log.info("autoreply | account=%s dry_run=%s max_replies=%s",
             account.get("name"), dry_run, max_replies)

    query = build_autoreply_query(cfg)
    log.info("autoreply query: %s", query)
    # pull a bit more than the cycle size — filters drop some of what X returns
    search_limit = min(40, max(10, int(cfg.get("tweets_per_cycle") or 20) * 2))
    tab = (cfg.get("sort") or "live")
    cooldown = max(0, int(cfg.get("cooldown_seconds") or 0))
    keyword = (cfg.get("keywords") or [""])[0]

    counts = {"found": 0, "qualified": 0, "reply": 0, "reply_fail": 0}
    found, seen_replies = [], set()
    logged_in = False
    status = "ok"  # ok | skipped | error
    exit_ip = ""

    llm = LLM({
        "api_key": settings["openai_api_key"],
        "model": settings.get("openai_model", "gpt-4o-mini"),
        "reply_system": cfg.get("system_prompt"),   # per-account personality
    }) if settings.get("openai_api_key") else None
    if not llm:
        # nothing to do without AI — don't waste a browser session
        log.error("no OpenAI key — cannot generate replies")
        return {"logged_in": False, "status": "error", "counts": {"found": 0,
                "error": "no OpenAI key configured (Settings)"}, "query": query,
                "found": [], "run_log": run_log, "exit_ip": "",
                "dry_run_report": None}

    def emit(msg, level="info"):
        """Step-by-step live feed: every pipeline step is written to ar_log the
        moment it happens (scheduler wires this to db.ar_log_add)."""
        fn = account.get("on_event")
        if fn:
            try:
                fn(msg, level)
            except Exception:
                pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=account.get("headless", True),
                                    slow_mo=account.get("slow_mo_ms", 0),
                                    proxy=account.get("proxy"), args=LAUNCH_ARGS)
        context = _browser_context(browser, account)
        page = context.new_page()
        session = XSession(page, action_log, account.get("username", ""), dry_run, dry_log=dry_log)
        try:
            logged_in = bool(session.login(account["auth_token"], account["ct0"]).get("logged_in"))
            if not logged_in:
                log.error("login failed — cycle aborted")
                status = "error"
                counts["error"] = "login failed"
            else:
                res = session.search(query, limit=search_limit, tab=tab)
                tweets = res.get("results", [])
                counts["found"] = len(tweets)
                log.info("autoreply: %d tweets found for query", len(tweets))
                emit(f"search done: {len(tweets)} tweets · {query}")
                for tweet in tweets:
                    tweet_id = tweet["url"].rstrip("/").split("/")[-1]
                    author = tweet.get("author", "?")
                    rec = {"tweet_id": tweet_id, "url": tweet["url"], "author": tweet["author"],
                           "content": tweet.get("text", ""), "keyword": keyword,
                           "likes": tweet.get("likes", 0), "retweets": tweet.get("retweets", 0),
                           "replies": tweet.get("replies", 0), "reply": "",
                           "status": "skipped", "reason": "", "error": ""}
                    reason = autoreply_skip_reason(tweet, cfg, processed_ids)
                    if reason:
                        rec["reason"] = reason
                        emit(f"skip @{author}: {reason}")
                        found.append(rec)
                        continue
                    if counts["reply"] >= max_replies:
                        rec["reason"] = "cycle limit"   # deferred, not persisted
                        emit(f"defer @{author}: tweets per cycle reached")
                        found.append(rec)
                        continue
                    counts["qualified"] += 1
                    emit(f"@{author}: generating reply…")
                    try:
                        gen = llm.generate_reply(_autoreply_user_prompt(tweet))
                    except Exception as e:
                        rec["reason"] = f"llm error: {e}"
                        emit(f"llm error @{author}: {e}", "error")
                        found.append(rec)
                        continue
                    out = gen["output"]
                    out, vskip = autoreply_resolve_reply(out)
                    if vskip:
                        rec["reason"] = vskip
                        emit(f"skip @{author}: {vskip.removeprefix('llm skip: ')}")
                        found.append(rec)
                        continue
                    rec["reply"] = out
                    verr = autoreply_reply_error(out, cfg, seen_replies)
                    if verr:
                        rec["reason"] = f"validation: {verr}"
                        emit(f"reply rejected @{author}: {verr}")
                        found.append(rec)
                        continue
                    if cooldown and counts["reply"]:
                        secs = cooldown * random.uniform(0.8, 1.4)
                        if account.get("on_delay"):
                            try:
                                account["on_delay"](secs)
                            except Exception:
                                pass
                        emit(f"cooldown {secs:.0f}s → replying to @{author}")
                        log.info("cooldown %.0fs before reply to %s", secs, tweet_id)
                        time.sleep(secs)
                    rres = session.reply(tweet, out)
                    if rres["status"] == "ok":
                        rec["status"] = "replied"
                        counts["reply"] += 1
                        seen_replies.add(out.lower())
                        emit(f"replied to @{author}: {out[:100]}")
                        log.info("replied to %s: %s", tweet_id, out)
                    elif rres["status"] == "error":
                        rec["status"] = "failed"
                        rec["error"] = "post failed"
                        counts["reply_fail"] += 1
                        emit(f"reply failed @{author}: post failed", "error")
                        log.error("reply post failed for %s", tweet_id)
                    else:  # skipped (dry run / already engaged)
                        rec["reason"] = "dry_run: would reply"
                        emit(f"dry-run would reply @{author}: {out[:100]}")
                        log.info("dry-run reply to %s: %s", tweet_id, out)
                    found.append(rec)
                if account.get("proxy"):
                    exit_ip = _capture_exit_ip(page)
        finally:
            try:
                context.clear_cookies()
                page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch(e) {} }")
            except Exception:
                pass
            browser.close()

    dry_report = dry_log.report() if dry_log else None
    summary = {"action": "autoreply_summary", "query": query, "dry_run": dry_run,
               "counts": counts, "status": status, "exit_ip": exit_ip,
               "dry_run_report": dry_report}
    action_log.record(**summary)
    log.info("autoreply done: %s", summary)
    return {"logged_in": logged_in, "status": status, "counts": counts,
            "query": query, "found": found, "run_log": run_log,
            "exit_ip": exit_ip, "dry_run_report": dry_report}

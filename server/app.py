"""FastAPI backend: REST for accounts/settings/runs + scheduler control.

Run:  uvicorn server.app:app --reload   (project root on sys.path)
In production, serves the built frontend from web/dist.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import auth, db, proxy, scheduler
from server import logging_config

logging_config.setup_logging()

app = FastAPI(title="X Bot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    logging_config.fold_uvicorn()
    db.init_db()
    scheduler.start()
    if auth.auth_enabled() and not auth.password_configured():
        print("WARNING: auth is enabled but XBOT_UI_PASSWORD is not set — login is "
              "impossible. Set XBOT_UI_PASSWORD, or XBOT_AUTH_DISABLED=1 for dev.")


# ---- auth -------------------------------------------------------------

class LoginIn(BaseModel):
    password: str


@app.post("/api/login")
def login(body: LoginIn):
    if not auth.check_password(body.password):
        raise HTTPException(401, "wrong password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie("session", auth.make_session(), httponly=True,
                    samesite="lax", max_age=7 * 86400)
    return resp


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.get("/api/auth")
def auth_state():
    return {"auth_required": auth.auth_enabled()}


@app.middleware("http")
async def _require_auth(request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/auth"):
        if auth.auth_enabled() and not auth.verify_session(request.cookies.get("session")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# ---- request models ---------------------------------------------------

class AccountIn(BaseModel):
    name: str
    username: str = ""
    auth_token: str = ""
    ct0: str = ""
    keywords: list[str] = []
    mode: str = "search"
    daily_posts: int = 0
    daily_likes: int = 0
    daily_retweets: int = 0
    daily_replies: int = 0
    like_probability: float = 0.6
    retweet_probability: float = 0.4
    active: bool = True
    proxy_id: Optional[int] = None
    pop_min_likes: int = 0      # search_popularity mode only
    pop_min_replies: int = 0    # search_popularity mode only
    pop_tab: str = "live"       # search_popularity mode only: 'top' | 'live'


class AccountPatch(BaseModel):
    name: Optional[str] = None
    username: Optional[str] = None
    auth_token: Optional[str] = None
    ct0: Optional[str] = None
    keywords: Optional[list[str]] = None
    mode: Optional[str] = None
    daily_posts: Optional[int] = None
    daily_likes: Optional[int] = None
    daily_retweets: Optional[int] = None
    daily_replies: Optional[int] = None
    like_probability: Optional[float] = None
    retweet_probability: Optional[float] = None
    active: Optional[bool] = None
    proxy_id: Optional[int] = None
    pop_min_likes: Optional[int] = None
    pop_min_replies: Optional[int] = None
    pop_tab: Optional[str] = None


class SettingsIn(BaseModel):
    schedule_active: Optional[bool] = None
    day_start_hour: Optional[float] = None
    day_end_hour: Optional[float] = None
    reply_min_likes: Optional[int] = None
    dry_run: Optional[bool] = None
    headless: Optional[bool] = None
    like_probability: Optional[float] = None
    retweet_probability: Optional[float] = None
    min_delay_seconds: Optional[int] = None
    max_delay_seconds: Optional[int] = None
    raid_independent: Optional[bool] = None   # raids run immediately, warm-up unaffected
    raid_step_gap: Optional[int] = None       # extra seconds between raid steps
    openai_model: Optional[str] = None
    openai_post_system_prompt: Optional[str] = None
    openai_reply_system_prompt: Optional[str] = None
    openai_api_key: Optional[str] = None


class ProxyIn(BaseModel):
    name: str = ""        # empty -> db auto-names "proxy N"
    host: str
    port: int
    scheme: str = "http"  # http | socks5
    username: str = ""
    password: str = ""
    active: bool = True


class ProxyPatch(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    scheme: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    active: Optional[bool] = None


class TestProxyIn(BaseModel):
    host: str
    port: int
    scheme: str = "http"
    username: str = ""
    password: str = ""


class RaidIn(BaseModel):
    account_id: int
    tweet_id: str            # full URL or raw numeric id
    action: str              # like | retweet | reply
    reply_text: str = ""
    raid_id: Optional[int] = None   # set when this is a mass-raid step


class MassStartIn(BaseModel):
    tweet_id: str
    likes: int = 0
    retweets: int = 0
    replies: int = 0


class MassFinishIn(BaseModel):
    raid_id: int
    status: str = "completed"


class MassPlanIn(BaseModel):
    tweet_id: str            # full URL or raw numeric id
    likes: int = 0
    retweets: int = 0
    replies: int = 0


# ---- accounts ---------------------------------------------------------

@app.get("/api/accounts")
def list_accounts():
    return db.list_accounts()


@app.post("/api/accounts", status_code=201)
def create_account(body: AccountIn):
    aid = db.create_account(body.model_dump())
    return db.get_account(aid)


@app.get("/api/accounts/activity")
def accounts_activity():
    """Latest successful action per account — {account_id: {action, ts}}.
    Registered BEFORE /api/accounts/{aid} so 'activity' isn't captured by {aid}."""
    return db.last_activity_per_account()


@app.get("/api/accounts/{aid}")
def get_account(aid: int):
    acc = db.get_account(aid)
    if not acc:
        raise HTTPException(404, "account not found")
    return acc


@app.put("/api/accounts/{aid}")
def update_account(aid: int, body: AccountPatch):
    if not db.get_account(aid):
        raise HTTPException(404, "account not found")
    db.update_account(aid, body.model_dump(exclude_none=True))
    return db.get_account(aid)


@app.delete("/api/accounts/{aid}")
def delete_account(aid: int):
    db.delete_account(aid)
    return {"ok": True}


@app.post("/api/accounts/{aid}/run-now")
def run_now(aid: int):
    """Run this account's next pending action immediately."""
    if not db.get_account(aid):
        raise HTTPException(404, "account not found")
    sa = db.next_pending(aid)
    if not sa:
        return {"ok": False, "queued": False, "msg": "no pending actions today"}
    db.mark_scheduled(sa["id"], "running")
    scheduler.enqueue(aid, sa["action_type"], "manual", sa["id"])
    return {"ok": True, "queued": True, "action": sa["action_type"], "run_at": sa["run_at"]}


@app.post("/api/accounts/{aid}/replan")
def replan_account(aid: int):
    """Restart this account's action chain now (drops pending, schedules next)."""
    acc = db.get_account(aid)
    if not acc:
        raise HTTPException(404, "account not found")
    remaining = scheduler.restart_chain(acc)
    return {"ok": True, "remaining": remaining}


@app.get("/api/accounts/{aid}/schedule")
def account_schedule(aid: int):
    from datetime import datetime
    today = datetime.now(db.TZ).strftime("%Y-%m-%d")
    return db.list_account_schedule(aid, today)


@app.get("/api/accounts/{aid}/runs")
def account_runs(aid: int, limit: int = 20):
    return db.list_runs(aid, limit)


@app.get("/api/accounts/{aid}/actions")
def account_actions(aid: int, limit: int = 50):
    """Tail of this account's actions.jsonl (one JSON object per line)."""
    path = Path("logs") / str(aid) / "actions.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    import json
    return [json.loads(ln) for ln in lines[-limit:] if ln.strip()]


@app.get("/api/accounts/{aid}/today")
def account_today(aid: int):
    acc = db.get_account(aid) or {}
    done = db.todays_counts(aid)
    return {
        "done": done,
        "quota": {
            "posts": acc.get("daily_posts", 0),
            "likes": acc.get("daily_likes", 0),
            "retweets": acc.get("daily_retweets", 0),
            "replies": acc.get("daily_replies", 0),
        },
    }


@app.get("/api/activity")
def activity(limit: int = 200):
    """Newest runs across all accounts — powers the live activity feed."""
    return db.recent_activity(limit)


@app.delete("/api/activity/errors")
def clear_error_activity():
    """Delete all errored runs from the activity history."""
    db.clear_error_runs()
    return {"ok": True}


@app.get("/api/runs/{rid}/log")
def run_log(rid: int, tail: int = 200):
    """Tail of a single run's log file — tracebacks live here. Powers the
    expandable error detail in the activity feed. run_log is a server-generated
    path (logs/<acct>/run_<id>.log); we still confine reads to the logs dir."""
    row = db.get_run(rid)
    if not row or not row.get("run_log"):
        return {"found": False, "log": ""}
    logs_root = Path("logs").resolve()
    try:
        path = Path(row["run_log"]).resolve()
    except Exception:
        return {"found": False, "log": ""}
    try:
        path.relative_to(logs_root)
    except ValueError:
        return {"found": False, "log": ""}
    if not path.exists():
        return {"found": False, "log": ""}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"found": True, "log": "\n".join(lines[-int(tail):])}


# ---- raider (act on a specific tweet by id) --------------------------

def parse_tweet_id(raw):
    """Accept a full X post URL or a bare numeric id; return the numeric id or ''."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.search(r"/status/(\d+)", s)
    if m:
        return m.group(1)
    return s if re.fullmatch(r"\d+", s) else ""


@app.post("/api/raider/run")
def raid_run(body: RaidIn):
    """Run a like/retweet/reply on a specific tweet for one account. Sync route
    (threadpool) — runs one browser session and returns the result. The Raider
    page calls this once per selected account for real-time per-account progress."""
    tid = parse_tweet_id(body.tweet_id)
    if not tid:
        raise HTTPException(400, "invalid tweet URL or ID")
    if body.action not in ("like", "retweet", "reply"):
        raise HTTPException(400, "action must be like, retweet, or reply")
    if body.action == "reply" and not body.reply_text.strip():
        raise HTTPException(400, "reply text is required")
    if not db.get_account(body.account_id):
        raise HTTPException(404, "account not found")
    return scheduler.run_raid(body.account_id, tid, body.action, body.reply_text, body.raid_id)


@app.post("/api/raider/mass/start")
def raid_mass_start(body: MassStartIn):
    """Begin a mass raid: create the raid row (status='running') and return its id.
    Each step is then run via /api/raider/run with this raid_id; /mass/finish closes it."""
    tid = parse_tweet_id(body.tweet_id)
    if not tid:
        raise HTTPException(400, "invalid tweet URL or ID")
    requested = {"like": int(body.likes or 0), "retweet": int(body.retweets or 0), "reply": int(body.replies or 0)}
    rid = db.start_raid(tid, "mass", requested)
    return {"raid_id": rid}


@app.post("/api/raider/mass/finish")
def raid_mass_finish(body: MassFinishIn):
    db.finish_raid(body.raid_id, body.status or "completed")
    return {"ok": True}


@app.get("/api/raids")
def list_raids(limit: int = 50):
    """Raid execution history (single + mass), newest first, with account roll-up."""
    return db.list_raids(limit)


@app.post("/api/raider/mass/plan")
def raid_mass_plan(body: MassPlanIn):
    """Auto-select which accounts perform each action on a tweet (balanced, oldest
    last-action first, randomized within the top window). Returns the plan +
    eligibility + shortfalls. Execution is driven step-by-step by the client via
    /api/raider/run so progress streams live."""
    tid = parse_tweet_id(body.tweet_id)
    if not tid:
        raise HTTPException(400, "invalid tweet URL or ID")
    return scheduler.mass_plan(tid, body.likes, body.retweets, body.replies)


# ---- settings + status ------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def update_settings(body: SettingsIn):
    data = body.model_dump(exclude_none=True)
    db.update_settings(data)
    return db.get_settings()


@app.post("/api/scheduler/reschedule")
def reschedule():
    scheduler.reschedule()
    return scheduler.status()


@app.post("/api/scheduler/tick")
def trigger_tick():
    """Manually run one scheduled round now (all active accounts' slices)."""
    scheduler.run_tick_now()
    return {"ok": True, "queued": True}


@app.get("/api/status")
def status():
    return scheduler.status()


# ---- proxies ----------------------------------------------------------

@app.get("/api/proxies")
def list_proxies():
    return db.list_proxies()


@app.get("/api/proxies/log")
def proxy_log(limit: int = 50):
    return db.list_proxy_log(limit)


@app.delete("/api/proxies/log")
def clear_proxy_log():
    db.clear_proxy_log()
    return {"ok": True}


@app.post("/api/proxies", status_code=201)
def create_proxy(body: ProxyIn):
    pid = db.create_proxy(body.model_dump())
    return db.get_proxy(pid)


@app.post("/api/proxies/test")
def test_proxy(body: TestProxyIn):
    """Live diagnostic via Playwright (HTTP + HTTPS), WITHOUT touching the DB. The add
    form uses this so a proxy is only saved once it actually works."""
    results, ms = proxy.diagnose_proxy(
        body.host, body.port, body.username, body.password, body.scheme
    )
    ip = next((r["ip"] for r in results if r["ok"]), "")
    return {"alive": any(r["ok"] for r in results), "ip": ip, "ms": ms,
            "results": results}


@app.get("/api/proxies/{pid}")
def get_proxy(pid: int):
    """Single proxy with the password decrypted — used by the edit form so the
    password field can be shown."""
    p = db.get_proxy(pid, decrypt=True)
    if not p:
        raise HTTPException(404, "proxy not found")
    return p


@app.put("/api/proxies/{pid}")
def update_proxy(pid: int, body: ProxyPatch):
    if not db.get_proxy(pid):
        raise HTTPException(404, "proxy not found")
    db.update_proxy(pid, body.model_dump(exclude_none=True))
    return db.get_proxy(pid)


@app.delete("/api/proxies/{pid}")
def delete_proxy(pid: int):
    db.delete_proxy(pid)
    return {"ok": True}


@app.post("/api/proxies/{pid}/check")
def check_proxy(pid: int):
    """Health-check a saved proxy via Playwright: alive + exit IP, persisted to the log.

    Sync (`def`) route -> Starlette runs it in a threadpool, so the blocking browser
    work can't stall the event loop."""
    p = db.get_proxy(pid, decrypt=True)
    if not p:
        raise HTTPException(404, "proxy not found")
    scheme = p.get("scheme") or "http"
    results, ms = proxy.diagnose_proxy(
        p["host"], p["port"], p.get("username", ""), p.get("password", ""), scheme
    )
    https = next((r for r in results if r["target"] == "HTTPS"), {})
    alive = bool(https.get("ok"))         # the bot needs HTTPS — that's the real verdict
    ip = https.get("ip", "")
    error = https.get("error", "")
    detail = "\n".join(
        f"{r['target']}: {'OK ' + r['ip'] if r['ok'] else 'FAIL'} ({r['ms']}ms)"
        + (f" — {r['error']}" if r["error"] else "")
        for r in results
    )
    db.record_proxy_check(pid, alive, ip, error, scheme if alive else "", detail, ms,
                          p.get("name", ""))
    return {"alive": alive, "ip": ip, "error": error, "scheme": scheme,
            "ms": ms, "detail": detail, "results": results}


# ---- dry run (read-only debugging endpoints) -------------------------

DRY_RUN_DIR = Path("logs/dry-run")


@app.get("/api/dry-runs")
def dry_runs_list():
    """List all dry-run sessions (newest first), reading report.json from each."""
    if not DRY_RUN_DIR.exists():
        return []
    out = []
    for d in DRY_RUN_DIR.iterdir():
        if not d.is_dir():
            continue
        run_id = d.name
        report_path = d / "report.json"
        entry = {"run_id": run_id, "account": "", "ts": "", "status": "running",
                 "errors": 0, "steps_total": 0}
        if report_path.exists():
            try:
                rpt = json.loads(report_path.read_text(encoding="utf-8"))
                entry.update({
                    "account": rpt.get("account", ""),
                    "ts": rpt.get("ts", ""),
                    "status": "failed" if rpt.get("errors", 0) > 0 else "success",
                    "errors": rpt.get("errors", 0),
                    "steps_total": rpt.get("steps_total", 0),
                })
            except (ValueError, KeyError):
                pass
        else:
            entry["ts"] = datetime.now().isoformat(timespec="seconds")
        out.append(entry)
    out.sort(key=lambda x: x.get("ts", ""), reverse=True)
    return out


@app.get("/api/dry-runs/{run_id}/report")
def dry_run_report(run_id: str):
    rpt_path = DRY_RUN_DIR / run_id / "report.json"
    if not rpt_path.exists():
        raise HTTPException(404, "report not found")
    return json.loads(rpt_path.read_text(encoding="utf-8"))


@app.get("/api/dry-runs/{run_id}/steps")
def dry_run_steps(run_id: str):
    steps_path = DRY_RUN_DIR / run_id / "steps.jsonl"
    if not steps_path.exists():
        raise HTTPException(404, "steps not found")
    lines = steps_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


@app.get("/api/dry-runs/{run_id}/screenshot/{file}")
def dry_run_screenshot(run_id: str, file: str):
    shot_dir = (DRY_RUN_DIR / run_id / "screenshots").resolve()
    try:
        path = (shot_dir / file).resolve()
        path.relative_to(shot_dir)  # security: must be inside screenshots/
    except (ValueError, OSError):
        raise HTTPException(404, "not found")
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(path))


# ---- twitter auto reply -----------------------------------------------

AR_STATUSES = {"draft", "active", "paused", "stopped", "error"}
AR_NUM_KEYS = ("min_likes", "min_retweets", "min_replies", "max_age_hours",
               "tweets_per_cycle", "cooldown_seconds", "max_reply_chars")


def _sanitize_ar_config(cfg):
    """Trust boundary: the config dict comes from the client; coerce it so a bad
    type can never reach the scheduler tick (which would otherwise raise every
    minute). Numbers are clamped >= 0, interval >= 1, lists capped."""
    out = dict(cfg)
    # hourly/daily reply limits were removed (tweets_per_cycle is the only cap);
    # strip them from configs saved before the removal
    for k in ("max_replies_per_hour", "daily_reply_limit"):
        out.pop(k, None)
    for k in AR_NUM_KEYS:
        try:
            out[k] = max(0, int(float(out.get(k) or 0)))
        except (TypeError, ValueError):
            out[k] = 0
    try:
        out["interval_minutes"] = max(1, int(float(out.get("interval_minutes") or 10)))
    except (TypeError, ValueError):
        out["interval_minutes"] = 10
    for k in ("keywords", "exclude_keywords", "banned_words"):
        v = out.get(k)
        out[k] = [str(x)[:80] for x in v][:20] if isinstance(v, list) else []
    out["system_prompt"] = str(out.get("system_prompt") or "")[:8000]
    out["lang"] = str(out.get("lang") or "")[:8]
    out["sort"] = out.get("sort") if out.get("sort") in ("live", "top") else "live"
    return out


class AutoReplyAssignIn(BaseModel):
    account_id: int
    config: dict = {}


class AutoReplyPatchIn(BaseModel):
    status: Optional[str] = None
    config: Optional[dict] = None


@app.get("/api/autoreply/overview")
def autoreply_overview():
    """Stats + assigned accounts + assignable warm-up accounts (one call for the page)."""
    return db.ar_overview()


@app.post("/api/autoreply/accounts", status_code=201)
def autoreply_assign(body: AutoReplyAssignIn):
    if not db.get_account(body.account_id):
        raise HTTPException(404, "account not found")
    ar_id = db.ar_assign(body.account_id, _sanitize_ar_config(body.config))
    if not ar_id:
        raise HTTPException(409, "account is already assigned to auto reply")
    return {"ok": True, "ar_id": ar_id}


@app.put("/api/autoreply/accounts/{ar_id}")
def autoreply_update(ar_id: int, body: AutoReplyPatchIn):
    if not db.ar_get(ar_id):
        raise HTTPException(404, "auto reply assignment not found")
    if body.status is not None and body.status not in AR_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(AR_STATUSES)}")
    data = body.model_dump(exclude_none=True)
    if "config" in data:
        data["config"] = _sanitize_ar_config(data["config"])
    db.ar_update(ar_id, data)
    ar = db.ar_get(ar_id)
    if body.status and ar["next_run_at"]:
        db.ar_log_add(ar_id, ar["account_id"], f"automation {body.status}")
    return ar


@app.delete("/api/autoreply/accounts/{ar_id}")
def autoreply_delete(ar_id: int):
    if not db.ar_get(ar_id):
        raise HTTPException(404, "auto reply assignment not found")
    db.ar_delete(ar_id)
    return {"ok": True}


@app.post("/api/autoreply/accounts/{ar_id}/run")
def autoreply_run(ar_id: int):
    """Queue one cycle immediately (respects limits inside the worker)."""
    ok, msg = scheduler.autoreply_run_now(ar_id)
    if not ok:
        raise HTTPException(409, msg)
    return {"ok": True, "queued": True, "msg": msg}


@app.post("/api/autoreply/accounts/{ar_id}/reset")
def autoreply_reset(ar_id: int):
    """Clear the account's tweet history: duplicate guard + counters start over,
    so every tweet is eligible for replies again."""
    ar = db.ar_get(ar_id)
    if not ar:
        raise HTTPException(404, "auto reply assignment not found")
    cleared = db.ar_reset(ar_id)
    db.ar_log_add(ar_id, ar["account_id"], f"history reset — {cleared} tweets eligible again")
    return {"ok": True, "cleared": cleared}


@app.get("/api/autoreply/accounts/{ar_id}/log")
def autoreply_log(ar_id: int, limit: int = 200):
    if not db.ar_get(ar_id):
        raise HTTPException(404, "auto reply assignment not found")
    return db.ar_log_list(ar_id, limit)


@app.get("/api/autoreply/activity")
def autoreply_activity(limit: int = 100):
    """Live feed for the Auto Reply page: recent events across all accounts +
    which cycle is running right now (one poll)."""
    return {"current": scheduler.ar_current(), "items": db.ar_log_all(limit)}


# ---- static frontend (production) ------------------------------------

WEB_DIST = Path("web/dist")
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    def _index():
        return FileResponse(WEB_DIST / "index.html")

    @app.get("/{full_path:path}")
    def _spa(full_path: str):
        # serve real files under web/dist, else index.html (client routing)
        candidate = WEB_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")

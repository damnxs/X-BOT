"""FastAPI backend: REST for accounts/settings/runs + scheduler control.

Run:  uvicorn server.app:app --reload   (project root on sys.path)
In production, serves the built frontend from web/dist.
"""
import os
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


class SettingsIn(BaseModel):
    schedule_active: Optional[bool] = None
    day_start_hour: Optional[float] = None
    day_end_hour: Optional[float] = None
    reply_min_likes: Optional[int] = None
    reply_max_age_hours: Optional[float] = None
    dry_run: Optional[bool] = None
    headless: Optional[bool] = None
    like_probability: Optional[float] = None
    retweet_probability: Optional[float] = None
    min_delay_seconds: Optional[int] = None
    max_delay_seconds: Optional[int] = None
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


# ---- accounts ---------------------------------------------------------

@app.get("/api/accounts")
def list_accounts():
    return db.list_accounts()


@app.post("/api/accounts", status_code=201)
def create_account(body: AccountIn):
    aid = db.create_account(body.model_dump())
    return db.get_account(aid)


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
def activity(limit: int = 50):
    """Newest runs across all accounts — powers the live activity feed."""
    return db.recent_activity(limit)


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

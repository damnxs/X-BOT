"""FastAPI backend: REST for accounts/settings/runs + scheduler control.

Run:  uvicorn server.app:app --reload   (project root on sys.path)
In production, serves the built frontend from web/dist.
"""
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server import db, scheduler

app = FastAPI(title="X Bot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    db.init_db()
    scheduler.start()


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
    openai_system_prompt: Optional[str] = None
    openai_api_key: Optional[str] = None


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
    today = datetime.now().strftime("%Y-%m-%d")
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

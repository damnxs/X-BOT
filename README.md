# X (Twitter) Bot

A bot that drives **your own X accounts** via Playwright: **likes, retweets,
posts** driven by keyword search or timeline. Two ways to run it:

- **Web UI** (default) — manage **many accounts**, set per-account daily quotas,
  cookies encrypted at rest, scheduler spreads actions across the day.
- **CLI** — single account from `config.yaml` (handy for testing).

## What it does

- **Search** X for your keywords (or read your home timeline).
- **Like** and **retweet** — one engagement per tweet, split by weights.
- **Post** original tweets via OpenAI (optional — needs an API key).
- Every action logs one JSON line to `actions.jsonl` (selectors tried, LLM
  in/out, screenshot on failure) — paste it back to debug.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
cd web && npm install && cd ..     # only needed for the web UI
```

## Web UI (multi-account)

### Dev mode (two terminals)
```bash
# 1) backend + scheduler
python -m uvicorn server.app:app --reload --port 8000
# 2) frontend (proxies /api -> :8000)
cd web && npm run dev              # opens http://localhost:5173
```

### Prod mode (one process — backend serves the built UI)
```bash
cd web && npm run build && cd ..
python -m uvicorn server.app:app --port 8000   # open http://localhost:8000
```

### Using it
1. **Add account** — name, @username, paste `auth_token` + `ct0` (DevTools →
   Application → Cookies on x.com), keywords, and daily posts/likes/retweets.
2. **Settings** — set the schedule interval (minutes), toggle dry-run, and
   optionally add the global OpenAI key to enable posting.
3. **Dry run first** — leave *Dry run* on; click **Run now** on an account and
   open **Logs** — you'll see `would like` / `would retweet` and engage nothing.
4. Flip *Dry run* off to go live. The scheduler runs each active account every
   *interval* minutes, doing a slice of its daily quota each time, resetting at
   midnight.

### Where things live
- `data/bot.db` — accounts, settings, run history (cookies **encrypted**).
- `data/master.key` — Fernet master key (auto-generated; gitignored). Override
  with the `XBOT_MASTER_KEY` env var if you prefer.
- `logs/<account_id>/` — per-account `actions.jsonl`, run logs, `seen.json`,
  `screenshots/`.

## CLI (single account, config.yaml)
```bash
cp config.example.yaml config.yaml      # fill in cookies + keywords
python bot.py --dry-run                 # verify; engages nothing
python bot.py --no-dry-run --mode search
```

## Architecture
```
React (Vite, :5173)  ──/api──▶  FastAPI (:8000)
                                  ├── SQLite  data/bot.db
                                  ├── APScheduler  (spread cadence + run-now queue)
                                  └── xbot engine (Playwright)  — reused by bot.py too
```
`xbot/runner.py:execute_run(account, settings, log_dir)` is the single execution
path used by both the CLI and the server worker.

## Safety
- `dry_run` default on — nothing engages until you turn it off.
- Randomized delays + low per-run slices to avoid ban heat.
- `seen.json` per account prevents re-liking/retweeting the same tweet.
- Your own tweets and promoted tweets are skipped.

## Logs (the debugging deliverable)
Each `logs/<id>/actions.jsonl` line has `selectors_tried` and, on failure, an
`error.traceback` + `screenshot` path. When something breaks, paste that line
back (the web UI's **Logs** view shows the tail).

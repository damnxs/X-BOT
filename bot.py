"""CLI: run a single account from config.yaml. Thin wrapper over xbot.runner.

The server (server/app.py) uses the same execute_run() but sources accounts from
SQLite instead of config.yaml.

Examples:
  python bot.py --dry-run                 # verify auth + selectors; engages nothing
  python bot.py --mode search --no-dry-run # go live, keyword search
  python bot.py --headful                  # watch the browser
"""
import argparse
import os

from xbot import config as config_mod
from xbot.runner import execute_run


def main():
    ap = argparse.ArgumentParser(description="X/Twitter bot (single account from config.yaml)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mode", choices=["search", "timeline"], help="overrides behavior.mode")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", default=None)
    ap.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    ap.add_argument("--max-posts", type=int, default=None)
    ap.add_argument("--max-likes", type=int, default=None)
    ap.add_argument("--headful", action="store_true", help="force headless=false (watch)")
    args = ap.parse_args()

    if not os.path.exists(args.config):
        raise SystemExit(f"config not found: {args.config} — copy config.example.yaml to config.yaml")

    cfg = config_mod.validate(config_mod.load_config(args.config))
    behavior = cfg["behavior"]
    dry_run = args.dry_run if args.dry_run is not None else bool(behavior.get("dry_run", True))
    headless = cfg.get("playwright", {}).get("headless", True) if not args.headful else False

    settings = {
        "min_delay_seconds": behavior.get("min_delay_seconds", 30),
        "max_delay_seconds": behavior.get("max_delay_seconds", 90),
        "openai_model": cfg.get("openai", {}).get("model", "gpt-4o-mini"),
        "openai_system_prompt": cfg.get("openai", {}).get("system_prompt", ""),
    }
    if config_mod.has_openai_key(cfg):
        settings["openai_api_key"] = cfg["openai"]["api_key"]

    account = {
        "id": "cli",
        "name": cfg["x"].get("username", "cli"),
        "username": cfg["x"].get("username", ""),
        "auth_token": cfg["x"]["auth_token"],
        "ct0": cfg["x"]["ct0"],
        "keywords": behavior.get("keywords", []),
        "mode": args.mode or behavior.get("mode", "search"),
        "limits": {
            "max_posts": args.max_posts if args.max_posts is not None else behavior.get("max_posts", 0),
            "max_likes": args.max_likes if args.max_likes is not None else behavior.get("max_likes", 5),
            "max_retweets": behavior.get("max_retweets", 2),
        },
        "like_probability": behavior.get("like_probability", 0.6),
        "retweet_probability": behavior.get("retweet_probability", 0.4),
        "dry_run": dry_run,
        "headless": headless,
        "slow_mo_ms": cfg.get("playwright", {}).get("slow_mo_ms", 0),
    }
    result = execute_run(account, settings, log_dir="logs")
    if not result["logged_in"]:
        raise SystemExit("login failed — see logs/actions.jsonl + logs/screenshots/")


if __name__ == "__main__":
    main()

"""Config loading + validation. Env vars override file values where useful."""
import os
import yaml

_PLACEHOLDER_VALUES = {"PASTE_HERE", "sk-...", "", None}


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("openai", {})
    # OPENAI_API_KEY env wins over file
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        cfg["openai"]["api_key"] = env_key
    return cfg


def validate(cfg):
    """Raise SystemExit with a clear message if config can't run.

    Only X cookies are required — an OpenAI key is optional (without one the bot
    runs like/retweet-only). See has_openai_key().
    """
    x = cfg.get("x", {})
    for k in ("auth_token", "ct0"):
        if x.get(k) in _PLACEHOLDER_VALUES:
            raise SystemExit(f"config: x.{k} is not set — edit config.yaml (or its copy).")
    return cfg


def has_openai_key(cfg):
    """True if a usable OpenAI key is configured (file value or OPENAI_API_KEY env)."""
    key = (cfg.get("openai") or {}).get("api_key")
    return key not in _PLACEHOLDER_VALUES

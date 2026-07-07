"""Centralized logging -> logs/xbot.log.

One file to grab when debugging: app + xbot engine + uvicorn all land here. App/engine
namespaces run at DEBUG (verbose, useful when something breaks); third-party libs stay at
INFO to avoid drowning the log. Rotates so it can't grow unbounded.
"""
import logging
import logging.handlers
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _ROOT / "logs"
_LOG_FILE = _LOG_DIR / "xbot.log"
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"

_root_done = False


def setup_logging():
    """Attach a rotating file handler + console handler to the root logger. Idempotent."""
    global _root_done
    if _root_done:
        return
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(_FORMAT)
    fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)            # libraries stay quiet (INFO)
    root.addHandler(fh)
    root.addHandler(ch)
    # our own namespaces: full DEBUG detail when diagnosing
    for n in ("xbot", "server"):
        logging.getLogger(n).setLevel(logging.DEBUG)
    _root_done = True


def fold_uvicorn():
    """Re-point uvicorn/fastapi loggers at the root handler so their access + error lines
    also land in logs/xbot.log. Called from the startup event (after uvicorn has already
    installed its own handlers), so we clear theirs and let records propagate to root."""
    for n in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        lg = logging.getLogger(n)
        lg.handlers = []
        lg.propagate = True
        lg.setLevel(logging.INFO)
    logging.getLogger("xbot").info("uvicorn/fastapi logs folded into %s", _LOG_FILE)


def log_file_path() -> Path:
    return _LOG_FILE

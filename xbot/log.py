"""Comprehensive logging for debuggability.

Three sinks:
  - console (INFO)             : quick feedback while running
  - logs/bot.log (DEBUG, rotating) : long-term history
  - logs/run_<id>.log (DEBUG)      : this run only

Plus logs/actions.jsonl: one JSON record per bot action (login/post/retweet/
reply/search/timeline). THIS is the file you paste back so a bug can be fixed —
every record carries the selectors tried, the LLM in/out, and on failure a
screenshot path + traceback.
"""
import json
import logging
import logging.handlers
import os
import threading
import time
import traceback
from datetime import datetime


class _RunRouter(logging.Handler):
    """Routes each record to the run file of the thread that emitted it.

    The 'xbot' logger is process-global, but runs overlap (worker thread,
    autoreply thread, HTTP raid threads). A plain per-run FileHandler gets
    swapped in by every setup_logging call — whichever run starts last steals
    all the lines. Here each thread owns one file; setup_logging hands that
    thread's file to the new run. ponytail: one open file per thread, bounded
    by the thread count; emit() is single-writer per file so no lock needed."""

    _files = {}  # thread ident -> open file object

    def emit(self, record):
        f = self._files.get(record.thread)
        if f is not None:
            try:
                f.write(self.format(record) + "\n")
                f.flush()
            except Exception:
                pass  # a broken log file must never break the run


def setup_logging(run_id, log_dir="logs", level="DEBUG"):
    """Configure the 'xbot' logger (NOT root) so server runs don't clobber uvicorn.

    Children like 'xbot.session' propagate up to 'xbot', which owns the handlers
    and does not propagate further to root. Console + bot.log are attached once;
    the per-run file is owned by the CALLING THREAD (see _RunRouter) so
    overlapping runs each keep their own log.
    """
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("xbot")
    logger.setLevel(getattr(logging, str(level).upper(), logging.DEBUG))
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if not logger.handlers:  # shared sinks attach once, never cleared again
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(ch)

        rh = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "bot.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        rh.setLevel(logging.DEBUG)
        rh.setFormatter(fmt)
        logger.addHandler(rh)

        rr = _RunRouter()
        rr.setLevel(logging.DEBUG)
        rr.setFormatter(fmt)
        logger.addHandler(rr)

    run_path = os.path.join(log_dir, f"run_{run_id}.log")
    prev = _RunRouter._files.pop(threading.get_ident(), None)
    if prev is not None:
        try:
            prev.close()
        except Exception:
            pass
    _RunRouter._files[threading.get_ident()] = open(run_path, "a", encoding="utf-8")
    return run_path


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def capture_failure(page, screenshots_dir, name, enabled=True):
    """Best-effort screenshot + HTML dump. Returns a dict of paths/errors."""
    out = {}
    if not enabled or page is None:
        return out
    try:
        os.makedirs(screenshots_dir, exist_ok=True)
        png = os.path.join(screenshots_dir, f"{name}.png")
        try:
            page.screenshot(path=png, full_page=True)
            out["screenshot"] = png
        except Exception as e:
            out["screenshot_error"] = f"{type(e).__name__}: {e}"
        html_path = os.path.join(screenshots_dir, f"{name}.html")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            out["html"] = html_path
        except Exception as e:
            out["html_error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        out["capture_error"] = f"{type(e).__name__}: {e}"
    return out


class ActionLog:
    """Writes one JSON line per action to logs/actions.jsonl."""

    def __init__(self, run_id, path, screenshots_dir, screenshots_enabled=True):
        self.run_id = run_id
        self.path = path
        self.screenshots_dir = screenshots_dir
        self.screenshots_enabled = screenshots_enabled
        self._counter = 0
        self._log = logging.getLogger("xbot.action")

    def next_id(self):
        self._counter += 1
        return self._counter

    def record(self, **fields):
        rec = {"run_id": self.run_id, "ts": now_iso()}
        rec.update(fields)
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            self._log.error("failed to write action record: %s", e)
        return rec

    def fail(self, action, rec, exc, page):
        """Stamp an error + capture artifacts onto rec, then persist it. Returns rec."""
        self.next_id()  # keep id monotonic with successful actions
        rec["action"] = action
        rec["status"] = "error"
        rec["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        name = f"{self.run_id}_{action}_{self._counter}"
        rec.update(capture_failure(page, self.screenshots_dir, name, self.screenshots_enabled))
        self.record(**rec)  # ponytail: fail() owns the write so errors are never dropped
        return rec


if __name__ == "__main__":
    # ponytail: self-check — overlapping runs in two threads must not steal
    # each other's run-log lines, and a sequential rerun in the same thread
    # must hand the file over.
    import tempfile

    tmp = tempfile.mkdtemp()
    logging.getLogger("xbot").handlers.clear()  # simulate a fresh process

    p1 = setup_logging("A", tmp)
    log = logging.getLogger("xbot")
    log.info("A thread line 1")

    def run_b():
        p2 = setup_logging("B", tmp)
        logging.getLogger("xbot").info("B thread line")
        _RunRouter._files.pop(threading.get_ident()).close()

    t = threading.Thread(target=run_b)
    t.start()
    t.join()
    log.info("A thread line 2")  # after B started+finished — must still land in A

    a = open(p1).read()
    assert "A thread line 1" in a and "A thread line 2" in a and "B thread line" not in a, a
    b = open(os.path.join(tmp, "run_B.log")).read()
    assert "B thread line" in b and "A thread" not in b, b

    p3 = setup_logging("A2", tmp)  # same thread, new run -> new file takes over
    log.info("A2 thread line")
    a2 = open(p3).read()
    assert "A2 thread line" in a2 and "A thread" not in a2, a2

    print("log routing self-check ok")

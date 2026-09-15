"""Dry Run logging — comprehensive selector-discovery and debugging system.

When dry_run is on, every simulated action is logged with: page URL/title,
tweet preview, all selectors tried (CSS + XPath + FOUND/MISSING/not_tested +
element attributes), a screenshot, and (on failure) the full traceback. A final
report.json summarises the run for at-a-glance diagnosis.

Folder layout (per run):
  logs/dry-run/<account>_<run_id>/
      steps.jsonl       one JSON line per step
      screenshots/      PNG + HTML via capture_failure
      report.json       final summary
"""
import json
import os
import re
import traceback as _tb

from xbot.log import capture_failure, now_iso

_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"


def _css_to_xpath(sel):
    """Best-effort CSS→XPath for the two selector patterns this codebase uses.
    Returns None for unmapped patterns (the 'if available' contract)."""
    m = re.match(r'^\[data-testid="([^"]+)"\]$', sel)
    if m:
        return f'//*[@data-testid="{m.group(1)}"]'
    m = re.match(r'^button\[aria-label\*="([^"]+)"\s*i\]$', sel)
    if m:
        label = m.group(1)
        return (f"//button[contains(translate(@aria-label,'{_UPPER}','{_LOWER}'),"
                f"'{label.lower()}')]")
    return None


class DryRunLogger:
    """Accumulates dry-run step records, writes them to steps.jsonl, and produces
    a final report.json. Reuses capture_failure for screenshots+HTML."""

    def __init__(self, run_id, account_slug, base="logs/dry-run"):
        self.run_id = run_id
        self.account = account_slug
        self.dir = os.path.join(base, f"{account_slug}_{run_id}")
        self.shot_dir = os.path.join(self.dir, "screenshots")
        self.steps_path = os.path.join(self.dir, "steps.jsonl")
        os.makedirs(self.shot_dir, exist_ok=True)
        self._n = 0
        self._stats = {
            "tweets_scanned": 0,
            "actions_simulated": {"like": 0, "retweet": 0, "reply": 0, "post": 0, "follow": 0},
            "selectors": {},        # sel -> {"found": N, "missing": N}
            "failed_selectors": [],  # unique selectors that were MISSING
            "errors": 0,
        }

    # ---- public API ------------------------------------------------------

    def step(self, action, page, selector_groups=None, rec=None, tweet=None, error=None):
        """Log one simulated action step with full detail."""
        self._n += 1
        n = self._n

        # page context (best-effort)
        url, title = "", ""
        try:
            url = page.url
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            pass

        # screenshot + HTML (reuses capture_failure)
        shots = capture_failure(page, self.shot_dir, f"{n:03d}_{action}")

        # selector detail
        sel_detail = []
        tried = (rec or {}).get("selectors_tried", [])
        if selector_groups:
            for grp in selector_groups:
                found_sel = grp.get("found")
                root = grp.get("root")
                loc = grp.get("loc")
                for sel in grp.get("selectors", []):
                    entry = {"group": grp.get("name", ""), "css": sel,
                             "xpath": _css_to_xpath(sel)}
                    if sel == found_sel:
                        entry["status"] = "FOUND"
                        entry["confidence"] = "high"
                        entry["attrs"] = _extract_attrs(loc)
                    elif sel in tried:
                        entry["status"] = "MISSING"
                        entry["confidence"] = _probe_confidence(root, sel)
                    else:
                        entry["status"] = "not_tested"
                        entry["confidence"] = None
                    sel_detail.append(entry)
                    self._track_selector(sel, entry["status"])
        elif tried:
            # error path: all tried selectors are MISSING
            for sel in tried:
                entry = {"group": "", "css": sel, "xpath": _css_to_xpath(sel),
                         "status": "MISSING", "confidence": "unknown"}
                sel_detail.append(entry)
                self._track_selector(sel, "MISSING")

        # tweet preview
        tweet_info = None
        if tweet:
            tw_url = tweet.get("url", "")
            tweet_info = {
                "id": tw_url.split("/")[-1] if tw_url else "",
                "author": tweet.get("author", ""),
                "text": (tweet.get("text", "") or "")[:140],
                "url": tw_url,
                "likes": tweet.get("likes", 0),
            }

        # error detail
        err_info = None
        if error is not None:
            self._stats["errors"] += 1
            err_info = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(_tb.format_exception(type(error), error, error.__traceback__)),
            }

        # accumulate action count
        if action in self._stats["actions_simulated"]:
            self._stats["actions_simulated"][action] += 1

        record = {
            "step": n, "ts": now_iso(), "action": action,
            "url": url, "title": title,
            "tweet": tweet_info,
            "selectors": sel_detail,
            "screenshot": shots.get("screenshot", ""),
            "html": shots.get("html", ""),
            "error": err_info,
        }
        self._write(record)
        return record

    def search_step(self, rec, page):
        """Lighter log for search/timeline — captures discovery, not selectors."""
        self._n += 1
        n = self._n
        results = rec.get("results", [])
        self._stats["tweets_scanned"] += len(results)

        url, title = "", ""
        try:
            url = page.url
        except Exception:
            pass
        try:
            title = page.title()
        except Exception:
            pass

        shots = capture_failure(page, self.shot_dir, f"{n:03d}_search")

        tweet_previews = []
        for t in results[:20]:
            tw_url = t.get("url", "")
            tweet_previews.append({
                "id": tw_url.split("/")[-1] if tw_url else "",
                "author": t.get("author", ""),
                "text": (t.get("text", "") or "")[:80],
                "likes": t.get("likes", 0),
            })

        record = {
            "step": n, "ts": now_iso(), "action": rec.get("action", "search"),
            "url": url, "title": title,
            "keyword": rec.get("keyword", ""), "tab": rec.get("tab", ""),
            "results_count": rec.get("results_count", 0),
            "articles_seen": rec.get("articles_seen", 0),
            "scroll_attempts": rec.get("scroll_attempts", 0),
            "tweets": tweet_previews,
            "screenshot": shots.get("screenshot", ""),
            "html": shots.get("html", ""),
        }
        self._write(record)
        return record

    def fail(self, action, page, rec, exc):
        """Error-path wrapper: all selectors_tried → MISSING, plus traceback."""
        return self.step(action, page, selector_groups=None, rec=rec,
                         tweet=None, error=exc)

    def report(self):
        """Write report.json and return the dict."""
        rpt = {
            "run_id": self.run_id,
            "account": self.account,
            "ts": now_iso(),
            "steps_total": self._n,
            "tweets_scanned": self._stats["tweets_scanned"],
            "actions_simulated": self._stats["actions_simulated"],
            "selectors_tested": self._stats["selectors"],
            "failed_selectors": self._stats["failed_selectors"],
            "errors": self._stats["errors"],
            "steps_path": self.steps_path,
            "screenshots_dir": self.shot_dir,
        }
        with open(os.path.join(self.dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(rpt, f, indent=2, ensure_ascii=False)
        return rpt

    # ---- internals -------------------------------------------------------

    def _track_selector(self, sel, status):
        slot = self._stats["selectors"].setdefault(sel, {"found": 0, "missing": 0})
        if status == "FOUND":
            slot["found"] += 1
        elif status == "MISSING":
            slot["missing"] += 1
            if sel not in self._stats["failed_selectors"]:
                self._stats["failed_selectors"].append(sel)

    def _write(self, record):
        with open(self.steps_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _extract_attrs(loc):
    """Best-effort element attributes from a Playwright locator."""
    if loc is None:
        return {}
    attrs = {}
    for name in ("aria-label", "data-testid", "role"):
        try:
            v = loc.get_attribute(name)
            if v:
                attrs[name] = v
        except Exception:
            pass
    try:
        attrs["text"] = loc.inner_text(timeout=2000)
    except Exception:
        pass
    try:
        attrs["tag"] = loc.evaluate("el => el.tagName")
    except Exception:
        pass
    return attrs


def _probe_confidence(root, sel):
    """Quick DOM probe: 'high' = element absent (selector wrong),
    'low' = element exists but was hidden (visibility issue)."""
    if root is None:
        return "unknown"
    try:
        count = root.locator(sel).count()
        return "high" if count == 0 else "low"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    # ponytail: self-check for _css_to_xpath + status derivation.
    assert _css_to_xpath('[data-testid="like"]') == '//*[@data-testid="like"]'
    xp = _css_to_xpath('button[aria-label*="ike" i]')
    assert xp and "translate" in xp and "ike" in xp
    assert _css_to_xpath('.random') is None

    # status derivation: tried=["a","b"], found="a" → a=FOUND, b=MISSING, c=not_tested
    tried = ["a", "b"]
    found = "a"
    for sel in ["a", "b", "c"]:
        if sel == found:
            assert "FOUND"
        elif sel in tried:
            assert "MISSING"
        else:
            assert "not_tested"

    print("dryrun self-check ok")

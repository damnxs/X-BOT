"""Playwright automation against x.com.

Every public action self-logs one record to ActionLog. Selectors are tried in a
fallback chain (data-testid first, aria-label/text second) and every selector
attempted is recorded, so a future DOM break is self-documenting in actions.jsonl.
"""
import logging
import random
import re
import time
from urllib.parse import quote_plus

_log = logging.getLogger("xbot.session")


def _parse_count(label):
    """Parse an engagement count from an aria-label like '1,234 Likes' or '1.2K'."""
    if not label:
        return 0
    label = label.replace(",", "")
    m = re.search(r"([\d.]+)\s*([KkMm]?)", label)
    if not m:
        return 0
    try:
        v = float(m.group(1))
    except ValueError:
        return 0
    suf = m.group(2).lower()
    if suf == "k":
        v *= 1_000
    elif suf == "m":
        v *= 1_000_000
    return int(v)

# Stable anchors (data-testid has been X's most stable surface for years).
SEL_COMPOSE_BOX = '[data-testid="tweetTextarea_0"]'
SEL_POST_BUTTON = '[data-testid="tweetButton"]'
SEL_REPLY = '[data-testid="reply"]'
SEL_RETWEET = '[data-testid="retweet"]'
SEL_RETWEET_CONFIRM = '[data-testid="retweetConfirm"]'
SEL_LIKE = '[data-testid="like"]'
SEL_UNLIKE = '[data-testid="unlike"]'
SEL_TWEET_TEXT = '[data-testid="tweetText"]'
SEL_SEARCH_INPUT = '[data-testid="SearchBox_Search_Input"]'
SEL_SIDENAV_POST = '[data-testid="SideNav_NewTweet_Button"]'
# follow button on a profile lives inside this wrapper (swaps to Following state)
SEL_FOLLOW_WRAP = '[data-testid="placementTracking"]'

# X search tabs are selected by the `f` URL query param:
#   (no f)  -> Top        f=live  -> Latest     f=user -> People
#   f=image -> Photos     f=video -> Videos
# Top is the DEFAULT — the canonical, most-reliable way to land on Top is to OMIT
# `f` (an explicit `f=top` is non-canonical and ignored-by-default on some fronts).
# Map every supported tab so the active tab is deterministic + logged, not a guess.
_SEARCH_TAB_F = {
    "live": "live", "latest": "live",
    "user": "user", "people": "user",
    "image": "image", "photos": "image",
    "video": "video", "videos": "video",
    # "top" intentionally absent -> no f param (canonical Top)
}


def _search_url(keyword, tab):
    q = quote_plus(keyword)
    f = _SEARCH_TAB_F.get((tab or "live").lower())
    # src=typed_query is X's inert attribution param (added when a query is typed);
    # including it makes the URL match a real typed search. f selects the tab.
    return f"https://x.com/search?q={q}&src=typed_query" + (f"&f={f}" if f else "")


class XSession:
    def __init__(self, page, action_log, username, dry_run, dry_log=None):
        self.page = page
        self.action_log = action_log
        self.username = (username or "").lstrip("@").lower()
        self.dry_run = dry_run
        self.dry_log = dry_log

    # ---- helpers ---------------------------------------------------------

    def _find_first(self, root, selectors, rec, wait=10000):
        """Return (locator, selector) of the first visible selector. Logs all attempts."""
        last_err = None
        for sel in selectors:
            rec["selectors_tried"].append(sel)
            loc = root.locator(sel).first
            try:
                loc.wait_for(state="visible", timeout=wait)
                return loc, sel
            except Exception as e:
                last_err = e
                continue
        raise RuntimeError(
            f"no selector found ({len(selectors)} tried); last error: {last_err}"
        )

    def _click_first(self, root, selectors, rec, wait=10000):
        loc, sel = self._find_first(root, selectors, rec, wait)
        loc.click()
        return sel

    def _click_publish(self, locator, rec):
        """Click the tweet/post button reliably.

        X's compose dialog sometimes layers a div that Playwright's actionability
        check flags as intercepting pointer events, so a plain .click() times out.
        Fall back to el.click() — dispatching the click directly on the button
        (bypasses hit-testing) and bubbling to X's React handler. force=True is
        deliberately avoided: it would silently click the overlay instead.
        """
        try:
            locator.click(timeout=5000)
            rec["publish_method"] = "click"
            return
        except Exception:
            rec["selectors_tried"].append(f"{SEL_POST_BUTTON} (el.click fallback)")
        locator.evaluate("el => el.click()")
        rec["publish_method"] = "el.click"

    def _finish(self, rec, start, exc=None):
        rec["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        if exc is not None:
            if self.dry_log:
                self.dry_log.fail(rec["action"], self.page, rec, exc)
            self.action_log.fail(rec["action"], rec, exc, self.page)
        else:
            self.action_log.record(**rec)
        return rec

    def _focused_article(self, tweet):
        """The <article> for the focused tweet on a status page.

        article.first is wrong: X may render a "This Post is unavailable."
        tombstone or a thread parent as the first article. The focused tweet is
        the article whose permalink points at its own status id.
        """
        status_id = tweet["url"].split("?")[0].rstrip("/").split("/")[-1]
        return self.page.locator(f'article:has(a[href*="/status/{status_id}"])').first

    # ---- warm-up: human-flow navigation ----------------------------------

    def _human_pause(self, lo=0.8, hi=2.5):
        """Short dwell between navigations so the flow reads as browsing, not a
        bot teleporting between pages."""
        self.page.wait_for_timeout(random.uniform(lo, hi) * 1000)

    def _goto_tweet(self, tweet):
        """Walk into a tweet the way a person does: landing page -> home feed ->
        permalink. Deep-linking straight into a status page is the classic bot
        tell, so every like/retweet/reply enters through the front door."""
        for url in ("https://x.com", "https://x.com/home"):
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            self._human_pause()
        self.page.goto(tweet["url"], wait_until="domcontentloaded", timeout=45000)

    # ---- auth ------------------------------------------------------------

    def login(self, auth_token, ct0):
        rec = {"action": "login", "status": "ok", "logged_in": False, "selectors_tried": []}
        start = time.monotonic()
        signal = self.page.locator(f"{SEL_SIDENAV_POST}, {SEL_COMPOSE_BOX}").first
        # strip whitespace/newlines — common when copying from DevTools
        auth_token = (auth_token or "").strip()
        ct0 = (ct0 or "").strip()
        try:
            # set cookies on both domains — X serves x.com and twitter.com, and some
            # sessions need the twitter.com cookie to be accepted.
            cookies = []
            for domain in (".x.com", ".twitter.com"):
                cookies.append({"name": "auth_token", "value": auth_token, "domain": domain,
                                "path": "/", "httpOnly": True, "secure": True, "sameSite": "None"})
                cookies.append({"name": "ct0", "value": ct0, "domain": domain,
                                "path": "/", "httpOnly": False, "secure": True, "sameSite": "Lax"})
            self.page.context.add_cookies(cookies)
            # try up to 2 times: X sometimes needs a reload after cookie injection,
            # and the compose button can be slow to render through a proxy.
            for attempt in range(2):
                self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
                try:
                    signal.wait_for(state="visible", timeout=25000)
                except Exception:
                    if attempt == 0:
                        _log.warning("login: compose button not visible — reloading once…")
                        self.page.wait_for_timeout(2000)
                        continue
                    raise
                rec["logged_in"] = True
                _log.info("login: OK (cookies accepted)%s", " (after reload)" if attempt else "")
                return self._finish(rec, start)
        except Exception as e:
            try:
                url = self.page.url
            except Exception:
                url = "?"
            # if X redirected to the landing/login page, the cookies are expired
            if url.rstrip("/").endswith(("x.com", "twitter.com")) or "/login" in url or "/flow" in url:
                _log.error("login: COOKIES REJECTED — X redirected to %s (not logged in). "
                           "auth_token len=%d, ct0 len=%d. If lengths look right (~40/~32), "
                           "X may have flagged this session from the proxy IP — re-grab cookies "
                           "while logged in through the SAME proxy.", url, len(auth_token), len(ct0))
            else:
                _log.error("login: FAILED — %s (url=%s)", e, url)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- post ------------------------------------------------------------

    def post(self, text):
        rec = {"action": "post", "text": text, "status": "ok", "selectors_tried": []}
        start = time.monotonic()
        try:
            self.page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45000)
            box = self.page.locator(SEL_COMPOSE_BOX).first
            box.wait_for(state="visible", timeout=15000)
            box.click()
            self.page.wait_for_timeout(300)
            self.page.keyboard.type(text, delay=12)
            rec["selectors_tried"].append(SEL_POST_BUTTON)
            self.page.locator(SEL_POST_BUTTON).first.wait_for(state="visible", timeout=8000)
            if self.dry_run:
                if self.dry_log:
                    post_btn = self.page.locator(SEL_POST_BUTTON).first
                    self.dry_log.step("post", self.page,
                        selector_groups=[
                            {"name": "compose_box", "root": self.page,
                             "selectors": [SEL_COMPOSE_BOX],
                             "found": SEL_COMPOSE_BOX, "loc": box},
                            {"name": "post_button", "root": self.page,
                             "selectors": [SEL_POST_BUTTON],
                             "found": SEL_POST_BUTTON, "loc": post_btn},
                        ], rec=rec, tweet=None)
                rec["status"] = "skipped"
                rec["note"] = "dry_run: would post"
                _log.info("dry-run post: %s", text)
            else:
                self._click_publish(self.page.locator(SEL_POST_BUTTON).first, rec)
                self.page.wait_for_timeout(2500)
                _log.info("posted: %s", text)
        except Exception as e:
            _log.error("post failed: %s", e)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- retweet ---------------------------------------------------------

    def retweet(self, tweet):
        rec = {
            "action": "retweet",
            "target": tweet,
            "status": "ok",
            "selectors_tried": [],
        }
        start = time.monotonic()
        try:
            self._goto_tweet(tweet)
            main = self._focused_article(tweet)
            main.wait_for(state="visible", timeout=15000)
            rt_sel = self._click_first(main, [SEL_RETWEET, 'button[aria-label*="epost" i]', 'button[aria-label*="etweet" i]'], rec)
            # confirm button lives in the overlay menu -> scope to page
            confirm = self.page.locator(SEL_RETWEET_CONFIRM).first
            confirm.wait_for(state="visible", timeout=10000)
            if self.dry_run:
                if self.dry_log:
                    self.dry_log.step("retweet", self.page,
                        selector_groups=[
                            {"name": "retweet_button", "root": main,
                             "selectors": [SEL_RETWEET, 'button[aria-label*="epost" i]', 'button[aria-label*="etweet" i]'],
                             "found": rt_sel, "loc": None},
                            {"name": "confirm_button", "root": self.page,
                             "selectors": [SEL_RETWEET_CONFIRM],
                             "found": SEL_RETWEET_CONFIRM, "loc": confirm},
                        ], rec=rec, tweet=tweet)
                rec["status"] = "skipped"
                rec["note"] = "dry_run: would retweet"
                self.page.keyboard.press("Escape")
                _log.info("dry-run retweet: %s", tweet["url"])
            else:
                confirm.click()
                self.page.wait_for_timeout(2000)
                _log.info("retweeted: %s", tweet["url"])
        except Exception as e:
            _log.error("retweet failed (%s): %s", tweet.get("url"), e)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- reply -----------------------------------------------------------

    def reply(self, tweet, text):
        rec = {
            "action": "reply",
            "target": tweet,
            "text": text,
            "status": "ok",
            "selectors_tried": [],
        }
        start = time.monotonic()
        try:
            self._goto_tweet(tweet)
            main = self._focused_article(tweet)
            main.wait_for(state="visible", timeout=15000)
            rp_sel = self._click_first(main, [SEL_REPLY, 'button[aria-label*="eply" i]'], rec)
            box = self.page.locator(SEL_COMPOSE_BOX).first
            box.wait_for(state="visible", timeout=8000)
            if self.dry_run:
                if self.dry_log:
                    self.dry_log.step("reply", self.page,
                        selector_groups=[
                            {"name": "reply_button", "root": main,
                             "selectors": [SEL_REPLY, 'button[aria-label*="eply" i]'],
                             "found": rp_sel, "loc": None},
                            {"name": "compose_box", "root": self.page,
                             "selectors": [SEL_COMPOSE_BOX],
                             "found": SEL_COMPOSE_BOX, "loc": box},
                        ], rec=rec, tweet=tweet)
                rec["status"] = "skipped"
                rec["note"] = "dry_run: would reply"
                self.page.keyboard.press("Escape")
                _log.info("dry-run reply to %s: %s", tweet["url"], text)
            else:
                # X opens reply in a modal whose backdrop (data-testid=mask) makes
                # Playwright's hit-test think it intercepts the compose box, so a
                # plain .click() times out. Fall back to a JS-dispatched click, which
                # focuses the contenteditable directly (bypasses hit-testing).
                try:
                    box.click(timeout=5000)
                except Exception:
                    rec["selectors_tried"].append(f"{SEL_COMPOSE_BOX} (el.click fallback)")
                    box.evaluate("el => el.click()")
                self.page.wait_for_timeout(300)
                self.page.keyboard.type(text, delay=12)
                self.page.locator(SEL_POST_BUTTON).first.wait_for(state="visible", timeout=8000)
                self._click_publish(self.page.locator(SEL_POST_BUTTON).first, rec)
                self.page.wait_for_timeout(2500)
                _log.info("replied to: %s", tweet["url"])
        except Exception as e:
            _log.error("reply failed (%s): %s", tweet.get("url"), e)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- like ------------------------------------------------------------

    def like(self, tweet):
        rec = {"action": "like", "target": tweet, "status": "ok", "selectors_tried": []}
        start = time.monotonic()
        try:
            self._goto_tweet(tweet)
            main = self._focused_article(tweet)
            main.wait_for(state="visible", timeout=15000)
            # X swaps the testid to "unlike" once liked — don't double-toggle.
            rec["selectors_tried"].append(SEL_UNLIKE)
            if main.locator(SEL_UNLIKE).count():
                rec["status"] = "skipped"
                rec["note"] = "already liked"
                _log.info("already liked: %s", tweet["url"])
                return self._finish(rec, start)
            loc, sel = self._find_first(main, [SEL_LIKE, 'button[aria-label*="ike" i]'], rec)
            if self.dry_run:
                if self.dry_log:
                    self.dry_log.step("like", self.page,
                        selector_groups=[{"name": "like_button", "root": main,
                            "selectors": [SEL_LIKE, 'button[aria-label*="ike" i]'],
                            "found": sel, "loc": loc}],
                        rec=rec, tweet=tweet)
                rec["status"] = "skipped"
                rec["note"] = "dry_run: would like"
                _log.info("dry-run like: %s", tweet["url"])
            else:
                loc.click()
                self.page.wait_for_timeout(1500)
                _log.info("liked: %s", tweet["url"])
        except Exception as e:
            _log.error("like failed (%s): %s", tweet.get("url"), e)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- warm-up: author follows -----------------------------------------

    def follow(self, username):
        """Follow one account from their profile page. The profile visit (vs.
        a follow-in-list click) keeps the warm-up browsing pattern human."""
        username = (username or "").lstrip("@")
        rec = {"action": "follow", "target_user": username, "status": "ok", "selectors_tried": []}
        start = time.monotonic()
        selectors = [f"{SEL_FOLLOW_WRAP} button", 'button[aria-label^="Follow" i]']
        try:
            self.page.goto(f"https://x.com/{username}", wait_until="domcontentloaded", timeout=45000)
            self._human_pause()
            btn, sel = self._find_first(self.page, selectors, rec)
            # X swaps the button to its "Following" state once followed — don't double-follow
            label = (btn.get_attribute("aria-label") or "").lower()
            if "following" in label:
                rec["status"] = "skipped"
                rec["note"] = "already following"
                _log.info("already following @%s", username)
            elif self.dry_run:
                if self.dry_log:
                    self.dry_log.step("follow", self.page,
                        selector_groups=[{"name": "follow_button", "root": self.page,
                            "selectors": selectors, "found": sel, "loc": btn}],
                        rec=rec, tweet=None)
                rec["status"] = "skipped"
                rec["note"] = "dry_run: would follow"
                _log.info("dry-run follow: @%s", username)
            else:
                btn.click()
                self.page.wait_for_timeout(1500)
                _log.info("followed @%s", username)
        except Exception as e:
            _log.error("follow failed (@%s): %s", username, e)
            return self._finish(rec, start, exc=e)
        return self._finish(rec, start)

    # ---- discovery -------------------------------------------------------

    def search(self, keyword, limit=10, tab="live"):
        rec = {
            "action": "search",
            "keyword": keyword,
            "tab": tab,
            "status": "ok",
            "selectors_tried": [],
            "results_count": 0,
        }
        start = time.monotonic()
        try:
            url = _search_url(keyword, tab)
            rec["url"] = url
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            tweets = self._collect_tweets(limit, rec)
            rec["results"] = tweets
            rec["results_count"] = len(tweets)
            _log.info("search '%s' [tab=%s]: %d tweets", keyword, tab, len(tweets))
        except Exception as e:
            _log.error("search '%s' [tab=%s] failed: %s", keyword, tab, e)
            return self._finish(rec, start, exc=e)
        if self.dry_log:
            self.dry_log.search_step(rec, self.page)
        return self._finish(rec, start)

    def timeline(self, limit=10):
        rec = {"action": "timeline", "status": "ok", "selectors_tried": [], "results_count": 0}
        start = time.monotonic()
        try:
            self.page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)
            tweets = self._collect_tweets(limit, rec)
            rec["results"] = tweets
            rec["results_count"] = len(tweets)
            _log.info("timeline: %d tweets", len(tweets))
        except Exception as e:
            _log.error("timeline failed: %s", e)
            return self._finish(rec, start, exc=e)
        if self.dry_log:
            self.dry_log.search_step(rec, self.page)
        return self._finish(rec, start)

    def _collect_tweets(self, limit, rec):
        """Scroll the feed and parse up to `limit` tweets.

        ponytail: O(articles * scrolls) re-scan each scroll; fine for small limits,
        switch to incremental tracking if limits grow large.
        """
        out = []
        seen = set()
        for attempt in range(8):
            articles = self.page.locator("article").all()
            for art in articles:
                if limit and len(out) >= limit:
                    break
                try:
                    if not art.locator(SEL_TWEET_TEXT).count():
                        continue
                    text = art.locator(SEL_TWEET_TEXT).first.inner_text(timeout=2000).strip()
                    link = art.locator('a[href*="/status/"]').first
                    href = link.get_attribute("href") if link.count() else ""
                    if not href or not href.startswith("/"):
                        continue
                    author = href.strip("/").split("/")[0].lower()
                    url = "https://x.com" + href.split("?")[0]
                    if url in seen:
                        continue
                    if author == self.username:
                        continue
                    if art.locator('span:has-text("Promoted")').count():
                        continue
                    posted = ""
                    time_el = art.locator("time").first
                    if time_el.count():
                        posted = time_el.get_attribute("datetime") or ""
                    likes = 0
                    like_btn = art.locator(SEL_LIKE).first
                    if like_btn.count():
                        likes = _parse_count(like_btn.get_attribute("aria-label"))
                    retweets = 0
                    rt_btn = art.locator(SEL_RETWEET).first
                    if rt_btn.count():
                        retweets = _parse_count(rt_btn.get_attribute("aria-label"))
                    replies_n = 0
                    rp_btn = art.locator(SEL_REPLY).first
                    if rp_btn.count():
                        replies_n = _parse_count(rp_btn.get_attribute("aria-label"))
                    seen.add(url)
                    out.append({"text": text, "author": author, "url": url,
                                "time": posted, "likes": likes,
                                "retweets": retweets, "replies": replies_n})
                except Exception:
                    continue
            if limit and len(out) >= limit:
                break
            self.page.mouse.wheel(0, 4000)
            self.page.wait_for_timeout(1200)
        rec["scroll_attempts"] = attempt + 1
        rec["articles_seen"] = len(articles)
        return out

"""Self-check: one RANDOM keyword per action. Gathering (like/retweet) and
reply-picking draw a single random keyword from the account's comma-separated
list each run — not all of them, and not always the same first one.
No browser needed (fake session)."""
import logging
import random

from xbot.runner import _gather_candidates, _first_candidate, _first_reply_candidate

logging.getLogger().addHandler(logging.NullHandler())
_log = logging.getLogger("xbot.test")


class FakeSession:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, q, limit=10, tab="live"):
        self.queries.append(q)
        return {"results": list(self.results)}


class FakeFeed(FakeSession):
    """Timeline-mode session: home feed, no search."""
    def timeline(self, limit=10):
        return {"results": list(self.results)}


kws = ["Solana", "Robinhood", "RWA"]

# gather: exactly ONE query per call, rotation covers the whole list
random.seed(3)
seen_queries = set()
for _ in range(30):
    s = FakeSession([{"url": "https://x.com/a/1", "likes": 1}])
    out = _gather_candidates(s, "search", kws, _log, 6)
    assert len(out) == 1 and len(s.queries) == 1
    seen_queries.add(s.queries[0])
assert seen_queries == set(kws), seen_queries
assert _gather_candidates(FakeSession([]), "search", [], _log, 6) == []  # no keywords -> no search

# reply: same single-random-keyword rule, most-liked wins
s = FakeSession([{"url": f"https://x.com/a/{i}", "likes": i} for i in range(5)])
best = _first_reply_candidate(s, "search", kws, set(), _log, 0)
assert best and best["likes"] == 4 and len(s.queries) == 1 and s.queries[0] in kws

# timeline mode: home feed only (no search), random pick among FRESH tweets
s = FakeFeed([{"url": f"https://x.com/a/{i}", "likes": 1, "author": f"u{i}"} for i in range(10)])
seen = {"https://x.com/a/0"}  # already engaged -> never picked again
random.seed(5)
picks = {_first_candidate(s, "timeline", [], seen, _log)["url"] for _ in range(60)}
assert picks == {f"https://x.com/a/{i}" for i in range(1, 10)}, sorted(picks)
assert not s.queries  # timeline never searches
pick = _first_reply_candidate(s, "timeline", [], seen, _log, 0)
assert pick and pick["url"] not in seen  # reply: random, but never an already-engaged tweet

print("ok: one random keyword per action · timeline = random fresh pick from feed")

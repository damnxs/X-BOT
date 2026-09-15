"""Self-check for the warm-up follow feature: a fresh randomized 1-in-3 roll per
engaged tweet decides whether that tweet's AUTHOR gets followed — random across
the whole stream (≈5–7 of 20), never everyone, no counting/waiting, each author
at most once. No browser needed (fake session)."""
import logging
import random

from xbot.runner import _warmup_follows, FOLLOW_ONE_IN

_log = logging.getLogger("xbot.warmup.test")
logging.getLogger().addHandler(logging.NullHandler())


class FakeSession:
    def __init__(self):
        self.followed = []

    def follow(self, username):
        self.followed.append(username)
        return {"status": "skipped", "note": "dry_run: would follow"}


def engage(sess, author, followed, i=1):
    counts = {}
    _warmup_follows(sess, {"author": author, "url": f"https://x.com/{author}/status/{i}"},
                    counts, _log, followed)
    return counts


# ~1 in 3 of 20 distinct authors (like the user's example) — random which ones
random.seed(7)
sess, followed = FakeSession(), set()
for i in range(200):  # 10 batches of the 20-tweet example
    engage(sess, f"u{i}", followed, i)
first_batch = list(sess.followed)
ratio = len(first_batch) / 200
assert 0.22 < ratio < 0.45, ratio
assert set(first_batch) <= {f"u{i}" for i in range(200)}

# an author skipped by the roll stays eligible and can be picked on a later tweet
random.seed(3)
sess, followed = FakeSession(), set()
picked = False
for i in range(60):
    if engage(sess, "repeatguy", followed, i).get("follow"):
        picked = True
    # keep engaging the same author until picked once
    if picked:
        break
assert picked and sess.followed.count("repeatguy") == 1
engage(sess, "repeatguy", followed, 99)   # already followed -> never again
assert sess.followed.count("repeatguy") == 1

# author we already follow on X (but not in our set) -> noted once, not counted
class AlreadyFollowing(FakeSession):
    def follow(self, username):
        super().follow(username)
        return {"status": "skipped", "note": "already following"}
sess, noted = AlreadyFollowing(), set()
random.seed(1)
for i in range(30):
    engage(sess, "oldfriend", noted, i)
assert sess.followed.count("oldfriend") == 1 and noted == {"oldfriend"}

# no author (raid-style /i/status URLs carry none) -> no crash, no follow
assert engage(FakeSession(), "", set()) == {}

print(f"warmup self-check ok ({len(first_batch)}/200 followed, ratio {ratio:.2f}, 1:{FOLLOW_ONE_IN})")

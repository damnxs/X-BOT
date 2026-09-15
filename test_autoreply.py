"""Self-check for the Twitter Auto Reply pure logic: query building, the filter
pipeline, and reply validation. Run: python test_autoreply.py"""
from datetime import datetime, timedelta, timezone

from xbot.runner import (autoreply_reply_error, autoreply_resolve_reply,
                         autoreply_skip_reason, build_autoreply_query)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def test_query():
    q = build_autoreply_query({
        "keywords": ["AI automation", "SaaS"],
        "exclude_keywords": ["job", "give away"],
        "lang": "en", "min_likes": 50, "min_retweets": 10, "min_replies": 5,
        "max_age_hours": 24,
    }, now=NOW)
    assert '"AI automation" OR SaaS' in q, q
    assert '-job' in q and '-"give away"' in q, q
    assert "min_faves:50" in q and "min_retweets:10" in q and "min_replies:5" in q, q
    assert "lang:en" in q and "since:2026-09-11" in q, q
    # empty config -> empty query, no crash
    assert build_autoreply_query({}) == ""


def test_skip_reasons():
    cfg = {"exclude_keywords": ["giveaway"], "min_likes": 50, "min_retweets": 10,
           "min_replies": 5, "max_age_hours": 24}
    base = {"url": "https://x.com/a/status/111", "text": "hello world", "author": "a",
            "time": NOW.isoformat(), "likes": 100, "retweets": 20, "replies": 10}
    ok = dict(base)
    assert autoreply_skip_reason(ok, cfg, set(), now=NOW) is None
    # every filter rejects in order: exclude, age, likes, retweets, replies, duplicate
    assert autoreply_skip_reason(dict(base, text="join my giveaway"), cfg, set(), now=NOW).startswith("excluded")
    old = (NOW - timedelta(hours=30)).isoformat()
    assert "age" in autoreply_skip_reason(dict(base, time=old), cfg, set(), now=NOW)
    assert "likes" in autoreply_skip_reason(dict(base, likes=10), cfg, set(), now=NOW)
    assert "retweets" in autoreply_skip_reason(dict(base, retweets=1), cfg, set(), now=NOW)
    assert "replies" in autoreply_skip_reason(dict(base, replies=0), cfg, set(), now=NOW)
    assert autoreply_skip_reason(base, cfg, {"111"}, now=NOW) == "already processed"
    # thresholds of 0 are disabled
    assert autoreply_skip_reason(dict(base, likes=0, retweets=0, replies=0),
                                 {"min_likes": 0, "min_retweets": 0, "min_replies": 0},
                                 set(), now=NOW) is None


def test_reply_validation():
    cfg = {"max_reply_chars": 20, "banned_words": ["discord"]}
    assert autoreply_reply_error("great thread!", cfg, set()) is None
    assert autoreply_reply_error("", cfg, set()) == "empty reply"
    assert "too long" in autoreply_reply_error("x" * 21, cfg, set())
    assert "banned" in autoreply_reply_error("join my discord", cfg, set())
    assert "duplicate" in autoreply_reply_error("Great Thread!", cfg, {"great thread!"})


def test_verdict_resolution():
    # a JSON-verdict prompt (decision/draft_reply) must never post its raw JSON
    skip = ('{"decision": "skip", "relevance_score": 20, "draft_reply": "N/A",'
            ' "reason": "memecoin promo"}')
    text, why = autoreply_resolve_reply(skip)
    assert text is None and why.startswith("llm skip: memecoin promo")
    ok = ('{"decision": "review", "draft_reply": "tokenized T-bills settle T+0, '
          'the custody chain is the hard part"}')
    text, why = autoreply_resolve_reply(ok)
    assert why is None and text.startswith("tokenized T-bills")
    # review without a usable draft -> skip; plain text and broken JSON pass through
    assert autoreply_resolve_reply('{"decision": "review", "draft_reply": "N/A"}')[1]
    assert autoreply_resolve_reply("nice thread!") == ("nice thread!", None)
    assert autoreply_resolve_reply("{not json") == ("{not json", None)


def test_ar_db():
    """DB round-trip on a throwaway DB: assign, dry-run retry semantics, upsert
    upgrade, date filter, zombie cleanup on account delete."""
    import os
    import tempfile

    from server import db as dbmod

    dbmod.DB_PATH = os.path.join(tempfile.mkdtemp(), "test.db")
    dbmod.init_db()
    aid = dbmod.create_account({"name": "t", "username": "t", "auth_token": "x", "ct0": "y"})
    ar_id = dbmod.ar_assign(aid)
    assert ar_id and dbmod.ar_assign(aid) is None

    def tweet(tid, status, reason="", reply=""):
        return {"tweet_id": tid, "url": f"https://x.com/a/status/{tid}", "author": "a",
                "content": "c", "keyword": "k", "likes": 1, "retweets": 1, "replies": 1,
                "status": status, "reason": reason, "reply": reply, "error": ""}

    # dry-run + llm-error tweets stay retryable; settled rows don't
    dbmod.ar_record_tweets(ar_id, aid, [
        tweet("1", "skipped", "dry_run: would reply", "draft"),
        tweet("2", "skipped", "llm error: boom"),
        tweet("3", "skipped", "likes 3 < 50"),
        tweet("4", "replied", "", "nice!"),
        tweet("5", "skipped", "cycle limit"),   # never persisted
    ])
    processed = dbmod.ar_processed_ids(ar_id)
    assert "1" not in processed and "2" not in processed, "dry-run/llm-error retryable"
    assert "3" in processed and "4" in processed and "5" not in processed

    # a real reply upgrades the dry-run row; a settled replied row never changes
    dbmod.ar_record_tweets(ar_id, aid, [
        tweet("1", "replied", "", "final!"),
        tweet("4", "skipped", "likes 0 < 50", ""),
    ])
    rows = {r["tweet_id"]: r for r in dbmod.ar_list_tweets(ar_id=ar_id)}
    assert rows["1"]["status"] == "replied" and rows["1"]["reply_text"] == "final!"
    assert rows["4"]["status"] == "replied" and rows["4"]["reply_text"] == "nice!"

    # overview must survive rows in ar_tweets (per-account stats used to arrive
    # as sqlite3.Row and crash _ar_row's .items() on the first recorded tweet)
    ov = dbmod.ar_overview()
    me = next(a for a in ov["accounts"] if a["ar_id"] == ar_id)
    assert me["found"] == 4 and me["replied"] == 2, me
    assert ov["stats"]["found"] == 4 and ov["stats"]["posted"] == 2

    # Jakarta calendar-date filter matches the stored +07:00 timestamps
    assert len(dbmod.ar_list_tweets(ar_id=ar_id, date=rows["1"]["created_at"][:10])) == 4

    # reset clears history: every tweet eligible again, counters back to zero
    assert dbmod.ar_reset(ar_id) == 4
    assert not dbmod.ar_processed_ids(ar_id)
    assert dbmod.ar_overview()["stats"]["found"] == 0

    # cross-account live feed returns log lines newest-first with the username
    dbmod.ar_log_add(ar_id, aid, "hello from the feed")
    feed = dbmod.ar_log_all(10)
    assert feed and feed[0]["message"] == "hello from the feed"
    assert feed[0]["account_username"] == "t"

    # deleting the warm-up account leaves no zombie automation behind
    dbmod.delete_account(aid)
    assert dbmod.ar_get(ar_id) is None and not dbmod.ar_list_tweets(ar_id=ar_id)
    assert dbmod.ar_stats()["found"] == 0


if __name__ == "__main__":
    test_query()
    test_skip_reasons()
    test_reply_validation()
    test_verdict_resolution()
    test_ar_db()
    print("autoreply logic: all checks passed")

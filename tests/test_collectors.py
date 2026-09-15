import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from xautoposter.collectors import APIError, Collector, XReader, normalize
from xautoposter.models import CollectionSource, Settings

from conftest import bundle, enable, import_post, raw_post


def run(coro):
    return asyncio.run(coro)


def source(db, **kw):
    return db.save_source(CollectionSource(name="研究", value="security", **kw).model_dump())


def counts_payload():
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)-timedelta(hours=1)
    return {"data": [{"start": (end-timedelta(hours=1)).isoformat(), "end": end.isoformat(), "tweet_count": 85}], "meta": {"total_tweet_count": 85}}


def test_official_search_counts_and_repeat_observation(db):
    enable(db)
    requests = []
    raw = raw_post()
    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.x.com"
        if request.url.path.endswith("counts/recent"):
            return httpx.Response(200, json=counts_payload())
        assert request.url.params["sort_order"] == "recency"
        assert "public_metrics" in request.url.params["tweet.fields"]
        assert "author_id" not in request.url.params.get("expansions", "")  # No extra user-read costs.
        return httpx.Response(200, json={"data": [raw], "meta": {"newest_id": "100"}})
    collector = Collector(db, XReader(db, "fake-token", httpx.MockTransport(respond)))
    src = source(db)
    first = run(collector.collect(src["id"]))
    assert first["status"] == "complete"
    assert first["received"] == 1
    data = db.data("x-live")
    assert data["dataset"]["provenance"] == "x_api"
    assert len(data["posts"]) == 1
    assert data["counts"][0]["count"] == 85
    assert data["posts"][0]["snapshots"][0]["impression_count"] is None
    assert db.usage()["day_usd"] == .01
    state = db.source(src["id"])["state"]
    state["next_allowed_at"] = "2020-01-01T00:00:00Z"
    db.source_state(src["id"], state)
    raw["public_metrics"]["like_count"] = 15
    second = run(collector.collect(src["id"]))
    assert second["status"] == "complete"
    assert len(db.data("x-live")["posts"]) == 1
    assert len(db.data("x-live")["posts"][0]["snapshots"]) == 2
    assert requests[2].url.params["since_id"] == "100"


def test_pagination_keeps_window_and_advances_since_only_on_completion(db):
    enable(db)
    search_requests = []
    def respond(request):
        if request.url.path.endswith("counts/recent"):
            return httpx.Response(200, json=counts_payload())
        search_requests.append(request)
        if len(search_requests) == 1:
            return httpx.Response(200, json={"data": [raw_post("200")], "meta": {"newest_id": "200", "next_token": "page-two"}})
        return httpx.Response(200, json={"data": [raw_post("190")], "meta": {"newest_id": "190"}})
    collector = Collector(db, XReader(db, "fake", httpx.MockTransport(respond)))
    src = source(db)
    result = run(collector.collect(src["id"]))
    assert result["complete"] is False
    state = db.source(src["id"])["state"]
    assert "since_id" not in state
    window = dict(state["pending_window"])
    state["next_allowed_at"] = "2020-01-01T00:00:00Z"
    db.source_state(src["id"], state)
    result = run(collector.collect(src["id"]))
    assert result["complete"] is True
    assert search_requests[-1].url.params["next_token"] == "page-two"
    assert search_requests[-1].url.params["end_time"] == window["end_time"]
    state = db.source(src["id"])["state"]
    assert state["since_id"] == "200"
    assert "next_token" not in state and "pending_window" not in state


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 500])
def test_api_errors_logged_without_secrets_or_false_success(db, status):
    enable(db)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(status, json={"detail": "fake-secret-do-not-echo"})
    collector = Collector(db, XReader(db, "fake-secret-do-not-echo", httpx.MockTransport(respond)))
    result = run(collector.collect(source(db)["id"]))
    assert result["status"] == "error"
    assert result["complete"] is False
    assert "fake-secret" not in str(result)
    assert len(calls) == 1
    assert not db.datasets()
    assert db.usage()["uncertain_requests"] == 1
    assert db.runs()[0]["body"]["status"] == "error"


def test_429_blocks_further_requests_and_does_not_retry(db):
    enable(db)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "120"})
    reader = XReader(db, "fake", httpx.MockTransport(respond))
    with pytest.raises(APIError, match="429"):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))
    with pytest.raises(APIError, match="停止"):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))
    assert len(calls) == 1
    assert db.config("api_cooldown")


def test_timeout_keeps_reservation_and_never_retries(db):
    enable(db)
    calls = []
    def respond(request):
        calls.append(request)
        raise httpx.ReadTimeout("do-not-echo-token", request=request)
    reader = XReader(db, "fake", httpx.MockTransport(respond))
    with pytest.raises(APIError, match="タイムアウト"):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))
    assert len(calls) == 1
    assert db.usage()["day_usd"] == .005
    assert db.usage()["uncertain_requests"] == 1


@pytest.mark.parametrize("settings,match", [({"daily_budget_usd": 0}, "日次予算"), ({"monthly_budget_usd": 0}, "月次予算"), ({"daily_post_limit": 0}, "読み取り上限")])
def test_budget_caps_prevent_network(db, settings, match):
    enable(db, **settings)
    calls = []
    reader = XReader(db, "fake", httpx.MockTransport(lambda r: calls.append(r)))
    with pytest.raises(APIError, match=match):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))
    assert not calls
    assert db.usage()["day_usd"] == 0


def test_budget_reservation_is_atomic(db):
    enable(db, daily_budget_usd=.005)
    def reserve(_):
        try:
            return db.reserve("/2/tweets", 1, 5000)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(reserve, range(8)))
    assert sum(t is not None for t in tickets) == 1
    assert db.usage()["day_usd"] == .005


def test_capped_counts_failure_keeps_search_and_marks_partial(db):
    # Reserve a full 10-result search at $.05, settle the one result to $.005;
    # pause during the response so the independent counts request cannot run.
    enable(db, max_posts_per_request=10)
    calls = []
    def respond(request):
        calls.append(request)
        db.set_config("settings", Settings(paused=True).model_dump())
        return httpx.Response(200, json={"data": [raw_post()], "meta": {"newest_id": "100"}})
    collector = Collector(db, XReader(db, "fake", httpx.MockTransport(respond)))
    result = run(collector.collect(source(db)["id"]))
    assert result["status"] == "partial"
    assert result["complete"] is False
    assert "停止" in result["error"]
    assert len(calls) == 1
    assert len(db.data("x-live")["posts"]) == 1


def test_update_interval_applies_to_manual_collection(db):
    enable(db)
    reader = XReader(db, "fake", httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})))
    collector = Collector(db, reader)
    src = source(db)
    run(collector.collect(src["id"]))
    with pytest.raises(APIError, match="更新間隔"):
        run(collector.collect(src["id"]))


def test_partial_metrics_and_malformed_posts_do_not_become_zero(db):
    raw = raw_post()
    raw["public_metrics"] = {"like_count": 10}
    posts, rejected = normalize({"data": [raw, {"id": "invalid"}]}, datetime.now(timezone.utc).isoformat(), "テーマ", "query")
    assert len(posts) == 1 and rejected == 1
    assert posts[0].snapshots[0].reply_count is None


def test_reply_quote_parent_relationships_and_read_only_endpoint_allowlist(db):
    enable(db)
    raw = raw_post()
    raw["referenced_tweets"] = [{"type": "replied_to", "id": "42"}]
    observed = datetime.now(timezone.utc).isoformat()
    posts, _ = normalize({"data": [raw]}, observed, "topic", "conversation_id:42")
    assert posts[0].kind == "reply" and posts[0].parent_id == "42"
    posts, _ = normalize({"data": [raw]}, observed, "topic", "quotes", forced_parent="50")
    assert posts[0].kind == "quote" and posts[0].parent_id == "50"
    reader = XReader(db, "fake")
    for endpoint in ("/2/dm_conversations", "/2/tweets/123/likes", "https://evil.example", "/2/tweets/../users"):
        with pytest.raises(APIError, match="許可"):
            run(reader.get(endpoint, {}))
    assert db.usage()["day_usd"] == 0


def test_missing_post_response_is_failure_not_zero(db):
    enable(db)
    reader = XReader(db, "fake", httpx.MockTransport(lambda r: httpx.Response(200, json={"errors": [{"title": "Not Found"}]})))
    with pytest.raises(APIError, match="0件成功"):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))


def test_redirect_never_followed(db):
    enable(db)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    reader = XReader(db, "fake", httpx.MockTransport(respond))
    with pytest.raises(APIError, match="302"):
        run(reader.get("/2/tweets", {"ids": "100"}, post_limit=1))
    assert len(calls) == 1


def test_selected_post_tracking_appends_timestamp_and_handles_missing_id(db):
    enable(db)
    p = import_post()
    db.ingest(bundle([p, import_post("200")]), "x-live", official=True)
    for id in ("100", "200"):
        db.save_analysis("x-live", id, True, "track", {})
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.path == "/2/tweets"
        assert set(request.url.params["ids"].split(",")) == {"100", "200"}
        return httpx.Response(200, json={"data": [raw_post("100", likes=50, created=p["created_at"])], "errors": [{"resource_id": "200"}]})
    collector = Collector(db, XReader(db, "fake", httpx.MockTransport(respond)))
    result = run(collector.observe())
    assert result["status"] == "partial"
    assert result["received"] == 1
    data = db.data("x-live")
    assert len(next(p for p in data["posts"] if p["id"] == "100")["snapshots"]) == 2
    assert len(next(p for p in data["posts"] if p["id"] == "200")["snapshots"]) == 1
    assert len(calls) == 1


def test_reply_and_quote_collection_keeps_partial_scope_and_cooldown(db):
    enable(db)
    db.ingest(bundle(), "x-live", official=True)
    calls = []
    def respond(request):
        calls.append(request)
        quote = request.url.path.endswith("quote_tweets")
        raw = raw_post("300" if quote else "301")
        raw["referenced_tweets"] = [{"type": "quoted" if quote else "replied_to", "id": "100"}]
        return httpx.Response(200, json={"data": [raw], "meta": {"next_token": "more"}})
    collector = Collector(db, XReader(db, "fake", httpx.MockTransport(respond)))
    result = run(collector.observe(["100"], responses=True))
    assert result["status"] == "partial" and result["received"] == 2
    assert len(db.data("x-live")["posts"]) == 3
    assert all(request.method == "GET" for request in calls)
    with pytest.raises(APIError, match="更新間隔"):
        run(collector.observe(["100"], responses=True))
    assert len(calls) == 2

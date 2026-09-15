from copy import deepcopy

from xautoposter.experiments import Draft, Experiments, outcome, report


def draft(**changes):
    return {"account": "@example", "topic": "検証", "goal": "開発者に検証手順を伝える",
            "hypothesis": "具体例で疑問に答える", "text": "形式テスト用の本文。",
            "horizon_hours": 24, **changes}


def create(client, **changes):
    response = client.post("/api/experiments", json=draft(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def action(client, e, action, **fields):
    return client.post(f"/api/experiments/{e['id']}/{action}", json={"version": e["version"], **fields})


def publish(client, e, id="111", at="2026-09-09T00:00:00Z"):
    result = action(client, e, "publication", url=f"https://x.com/example/status/{id}", published_at=at)
    assert result.status_code == 200, result.text
    return result.json()


def observe(client, e, at="2026-09-10T00:00:00Z", **values):
    result = action(client, e, "observations", observed_at=at, **values)
    assert result.status_code == 200, result.text
    return result.json()


def reflection(client, e):
    result = action(client, e, "reflection", finding="質問が届いた", evidence="24時間の返信数と抜粋",
                    alternative="投稿時刻の影響もある", next_change="次は実画面で説明する")
    assert result.status_code == 200, result.text
    return result.json()


def test_complete_learning_loop_persistence_and_lineage(client):
    first = create(client)
    assert first["publication"] is None
    published = publish(client, first)
    assert published["publication"]["provenance"] == "user_reported"
    measured = observe(client, published, reply_count=2, response_notes="例示の反応・未検証")
    reviewed = reflection(client, measured)
    next = action(client, reviewed, "iterate").json()
    assert next["parent_id"] == first["id"]
    assert next["draft"]["text"] == ""  # Do not duplicate the already-published text.
    assert next["draft"]["change"] == "次は実画面で説明する"
    assert next["publication"] is None and not next["observations"]
    assert next["parent_reflection"]["observations"][0]["reply_count"] == 2
    reloaded = Experiments(client.app.state.store).get(first["id"])
    assert reloaded["reflection"]["report"]["value"] == 2
    history = client.get(f"/api/experiments/{first['id']}/history").json()
    assert len(history) == 4 and history[-1]["body"]["publication"] is None
    assert client.get("/api/experiments").json()[0]["id"] == next["id"]


def test_dedup_conflicts_and_stale_review(client):
    e = observe(client, publish(client, create(client)), reply_count=2)
    e = reflection(client, e)
    duplicate = observe(client, e, reply_count=2)
    assert len(duplicate["observations"]) == 1 and not duplicate["review_stale"]
    conflict = action(client, duplicate, "observations", observed_at="2026-09-10T00:00:00Z", reply_count=99)
    assert conflict.status_code == 400
    changed = observe(client, duplicate, "2026-09-10T01:00:00Z", reply_count=5)
    assert changed["review_stale"]
    assert changed["reflection"]["observations"][0]["reply_count"] == 2
    assert action(client, changed, "iterate").status_code == 400
    assert action(client, reflection(client, changed), "iterate").status_code == 200


def test_published_content_and_metric_cannot_be_rewritten(client):
    e = publish(client, create(client))
    response = client.put(f"/api/experiments/{e['id']}", json={**draft(metric="like_count"), "version": e["version"]})
    assert response.status_code == 400
    second = create(client)
    assert action(client, second, "publication", url="https://twitter.com/example/status/111?s=20", published_at="2026-09-09T00:00:00Z").status_code == 400


def test_validation_unknowns_and_no_fake_early_metrics(client):
    e = create(client)
    assert action(client, e, "observations", observed_at="2026-09-10T00:00:00Z", reply_count=2).status_code == 400
    assert action(client, e, "publication", url="https://evil.test/status/111", published_at="2026-09-09T00:00:00Z").status_code == 422
    assert action(client, e, "publication", url="https://x.com/a/status/111", published_at="2099-09-09T00:00:00Z").status_code == 422
    e = publish(client, e)
    assert action(client, e, "observations", observed_at="2026-09-08T00:00:00Z", reply_count=2).status_code == 400
    assert action(client, e, "observations", observed_at="2026-09-10T00:00:00Z", reply_count=-1).status_code == 422
    assert action(client, e, "observations", observed_at="2026-09-10T00:00:00Z", reply_count=True).status_code == 422
    e = observe(client, e, "2026-09-11T00:00:00Z", reply_count=100)
    assert outcome(e)["value"] is None  # Late total cannot reconstruct the 24h value.
    e = observe(client, e, "2026-09-10T00:00:00Z", like_count=5)
    assert outcome(e)["value"] is None  # Missing replies are unknown, not zero.


def test_age_matched_comparisons_exclude_unlike_or_late_samples(client):
    original = observe(client, publish(client, create(client)), reply_count=2)
    improved = create(client)
    improved = observe(client, publish(client, improved, "222", "2026-09-10T00:00:00Z"), "2026-09-11T00:00:00Z", reply_count=5)
    improved["parent_id"] = original["id"]
    wrong = deepcopy(original)
    wrong["id"] = "wrong"
    wrong["draft"]["account"] = "another"
    r = report(improved, [original, improved, wrong])
    assert r["comparison_n"] == 1 and r["parent_difference"] == 3
    assert r["peers"][0]["age_minutes"] == 1440
    wrong["draft"]["account"] = original["draft"]["account"]
    wrong["observations"][0]["observed_at"] = "2026-09-10T00:31:00+00:00"
    assert report(improved, [wrong])["comparison_n"] == 0


def test_conflicting_edits_do_not_lose_changes(client):
    e = create(client)
    edited = client.put(f"/api/experiments/{e['id']}", json={**draft(text="変更後"), "version": e["version"]})
    assert edited.status_code == 200
    rejected = client.put(f"/api/experiments/{e['id']}", json={**draft(text="古いタブ"), "version": e["version"]})
    assert rejected.status_code == 400
    assert client.get("/api/experiments").json()[0]["draft"]["text"] == "変更後"


def test_four_metric_sum_and_history_survive_database_reopen(db):
    e = Experiments(db).create(Draft(**draft(metric="reactions")))
    assert Experiments(db).get(e["id"])["version"] == 1
    e["publication"] = {"published_at": "2026-09-09T00:00:00Z"}
    e["observations"] = [{"observed_at": "2026-09-10T00:00:00Z", "like_count": 10, "retweet_count": 2, "reply_count": 1}]
    assert outcome(e)["value"] is None
    e["observations"][0]["quote_count"] = 0
    assert outcome(e)["value"] == 13


def test_workflow_has_no_remote_send_capability(client):
    assert client.get("/api/session").json()["write_capabilities"] == []
    e = create(client)
    for route in ("publish", "send", "reply", "dm"):
        assert action(client, e, route).status_code == 404

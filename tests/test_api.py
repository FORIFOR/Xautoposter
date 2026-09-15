import json

from xautoposter.server import create_app
from xautoposter.store import Store

from conftest import import_post


def test_ui_and_empty_workspace(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Xautoposter" in response.text
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/api/datasets").json() == []
    assert client.get("/api/session").json()["write_capabilities"] == []


def test_sample_end_to_end_save_reload_and_report_export(client):
    result = client.post("/api/demo").json()
    assert result["added_posts"] == 29
    overview = client.get("/api/datasets/demo/overview").json()
    assert overview["dataset"]["provenance"] == "synthetic"
    detail = client.get("/api/datasets/demo/posts/demo-0-0").json()
    assert detail["comparison"]["ratio"] > 5
    saved = client.put("/api/datasets/demo/posts/demo-0-0/saved", json={"saved": True, "note": "比較条件をさらに確認する"})
    assert saved.status_code == 200
    later = client.get("/api/datasets/demo/posts/demo-0-0").json()
    assert later["post"]["saved"] is True
    assert later["post"]["note"] == "比較条件をさらに確認する"
    stored = client.get("/api/datasets/demo/posts/demo-0-0/saved").json()
    assert stored["report"]["method_version"] == detail["method_version"]
    json.dumps(stored, allow_nan=False)
    path = client.app.state.store.path
    assert Store(path).saved_report("demo", "demo-0-0")["note"] == "比較条件をさらに確認する"
    assert client.post("/api/demo").json()["duplicates"] > 0


def test_json_import_and_append_observations(client):
    req = {"format": "json", "name": "import", "provenance": "imported", "text": json.dumps({"posts": [import_post()]})}
    response = client.post("/api/import", json=req)
    assert response.status_code == 200
    dataset = response.json()["dataset"]
    req["dataset"] = dataset
    again = client.post("/api/import", json=req)
    assert again.json()["duplicates"] == 1
    req["text"] = json.dumps({"posts": [import_post(snapshots=[{"observed_at": "2026-09-10T02:00:00Z", "like_count": 20}])]})
    assert client.post("/api/import", json=req).json()["added_snapshots"] == 1
    report = client.get(f"/api/datasets/{dataset}/posts/100").json()
    assert len(report["post"]["snapshots"]) == 2
    assert report["observation"]["velocity"] is None


def test_mislabeled_sample_is_rejected(client):
    example = client.get("/api/import-template/json").text
    response = client.post("/api/import", json={"format": "json", "name": "bad", "provenance": "imported", "text": example})
    assert response.status_code == 400
    assert "種別" in response.json()["detail"]


def test_csv_and_json_templates_roundtrip(client):
    for format in ("csv", "json"):
        example = client.get("/api/import-template/" + format)
        assert "attachment" in example.headers["content-disposition"]
        response = client.post("/api/import", json={"format": format, "name": "test", "provenance": "synthetic", "text": example.text})
        assert response.status_code == 200, response.text
        assert response.json()["added_posts"] == 29


def test_settings_persist_token_does_not(client):
    settings = client.get("/api/settings").json()["settings"]
    settings.update(bearer_token="do-not-store-secret", daily_budget_usd=.75)
    assert client.put("/api/settings", json=settings).status_code == 200
    response = client.get("/api/settings")
    assert "do-not-store-secret" not in response.text
    assert response.json()["token_configured"] is True
    db = client.app.state.store
    assert "do-not-store-secret" not in db.path.read_bytes().decode("utf-8", errors="ignore")
    assert Store(db.path).settings().daily_budget_usd == .75


def test_collection_missing_key_rejects_without_network(client):
    source = client.post("/api/sources", json={"name": "query", "value": "security"}).json()
    response = client.post(f"/api/sources/{source['id']}/collect")
    assert response.status_code == 400
    assert client.app.state.store.usage()["day_usd"] == 0


def test_foreign_origins_host_and_missing_auth_rejected(client):
    assert client.get("/api/settings", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/session", headers={"Host": "evil.example"}).status_code == 403
    assert client.get("/api/settings", headers={"x-local-token": ""}).status_code == 401
    assert client.post("/api/demo", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_no_x_write_routes_or_paid_operations_from_analysis(client):
    for path in ("/api/publish", "/api/reply", "/api/dm", "/api/schedule", "/api/tweets"):
        assert client.post(path, json={"text": "test"}).status_code == 404
    client.post("/api/demo")
    client.get("/api/datasets/demo/overview")
    client.get("/api/datasets/demo/posts/demo-0-0")
    assert client.app.state.store.usage()["day_usd"] == 0


def test_source_validation_and_edit_reset_cursor(client):
    assert client.post("/api/sources", json={"kind": "post", "name": "bad", "value": "http://127.0.0.1/admin"}).status_code == 422
    source = client.post("/api/sources", json={"kind": "post", "name": "one", "value": "https://x.com/example/status/12345"}).json()
    assert source["value"] == "12345"
    db = client.app.state.store
    db.source_state(source["id"], {"next_token": "old", "since_id": "12"})
    changed = client.put("/api/sources/"+source["id"], json={"kind": "account", "name": "new", "value": "@example"}).json()
    assert changed["state"] == {}
    assert changed["value"] == "example"

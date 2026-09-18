from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from xautoposter.learning_server import create_app
from xautoposter.experiments import Draft, Publication
from xautoposter.models import ImportBundle, Post


def setup(tmp_path):
    app=create_app(tmp_path, run_scheduler=False, token="test-token")
    client=TestClient(app, base_url="http://127.0.0.1")
    headers={"X-Local-Token":client.get("/api/session").json()["token"]}
    workspace=app.state.learning.workspace
    e=workspace.create(Draft(account="alice",topic="test",goal="共有する",hypothesis="冒頭の具体例を変える",text="本文",metric="like_count"))
    now=datetime.now(timezone.utc)
    e=workspace.publish(e["id"],Publication(version=e["version"],url="https://x.com/alice/status/123",published_at=(now-timedelta(hours=24)).isoformat()))
    bundle=ImportBundle(name="official",posts=[Post(id="123",author_id="456",text="本文",topic="test",created_at=e["publication"]["published_at"],snapshots=[{"observed_at":now.isoformat(),"like_count":0}])])
    return app,client,headers,e,bundle


def test_security_and_new_screen(tmp_path):
    app,client,headers,e,bundle=setup(tmp_path)
    assert client.get("/learning").status_code==200
    assert client.get("/api/learning").status_code==401
    assert client.get("/api/learning",headers=headers).status_code==200
    assert client.get("/api/learning",headers={**headers,"Origin":"https://evil.invalid"}).status_code==403


def test_sync_alias_and_conflict(tmp_path):
    app,client,headers,e,bundle=setup(tmp_path)
    app.state.store.ingest(bundle,"x-live",official=True)
    r=client.post(f'/api/experiments/{e["id"]}/sync-x-api',headers=headers,json={"version":e["version"]})
    assert r.status_code==200, r.text
    assert r.json()["verified_publication"]["ownership_verified"] is False
    assert client.post(f'/api/learning/{e["id"]}/sync',headers=headers,json={"version":e["version"]}).status_code==400


def test_one_atomic_next_plan(tmp_path):
    app,client,headers,e,bundle=setup(tmp_path)
    loop=app.state.learning
    app.state.store.ingest(bundle,"x-live",official=True)
    e=loop.sync(e["id"],e["version"])
    child=loop.propose(e["id"],e["version"])
    assert loop.propose(e["id"])["id"]==child["id"]
    assert len(loop.workspace.all())==2
    assert child["publication"] is None and child["draft"]["text"]==""
    assert child["parent_evidence"]["measurement"]["value"]==0


def test_opt_in_tick_and_stop_after_horizon(tmp_path,monkeypatch):
    import asyncio
    app,client,headers,e,bundle=setup(tmp_path)
    loop=app.state.learning
    calls=[]
    async def collect(source_id):
        calls.append(source_id)
        app.state.store.ingest(bundle,"x-live",official=True)
        return {"status":"complete"}
    monkeypatch.setattr(loop.collector,"collect",collect)
    app.state.store.set_config("settings",{"paused":False})
    asyncio.run(loop.tick()); assert not calls
    loop.configure(e["id"],e["version"],True,True,15)
    asyncio.run(loop.tick()); asyncio.run(loop.tick())
    assert len(calls)==1 and len(loop.workspace.all())==2
    assert not loop.workspace.get(e["id"])["learning"]["enabled"]


def test_global_pause_blocks_automatic_collection(tmp_path,monkeypatch):
    import asyncio
    app,client,headers,e,bundle=setup(tmp_path)
    loop=app.state.learning
    loop.configure(e["id"],e["version"],True,True,15)
    async def forbidden(*args): raise AssertionError("must not collect while paused")
    monkeypatch.setattr(loop.collector,"collect",forbidden)
    asyncio.run(loop.tick())
    assert app.state.store.config("learning_runtime")["state"]=="paused"

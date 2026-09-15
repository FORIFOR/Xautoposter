from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from xautoposter.models import ImportBundle, Settings
from xautoposter.server import create_app
from xautoposter.store import Store


@pytest.fixture
def db(tmp_path):
    return Store(tmp_path / "test.sqlite3")


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, run_scheduler=False, token="")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        client.headers["x-local-token"] = client.get("/api/session").json()["token"]
        yield client


def raw_post(id="100", *, likes=10, author="900", created=None):
    return {"id": id, "author_id": author, "text": "例えば警告を比較して検証する。", "lang": "ja",
            "created_at": created or (datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),
            "public_metrics": {"like_count": likes, "retweet_count": 2, "reply_count": 1, "quote_count": 0}}


def import_post(id="100", *, created="2026-09-10T00:00:00Z", author="900", snapshots=None):
    return {"id": id, "author_id": author, "text": "検証した投稿。", "created_at": created, "topic": "研究", "lang": "ja", "media_type": "text",
            "snapshots": deepcopy(snapshots or [{"observed_at": "2026-09-10T01:00:00Z", "like_count": 10, "retweet_count": 2, "reply_count": 1, "quote_count": 0}])}


def bundle(posts=None, **kwargs):
    return ImportBundle.model_validate({"name": "test", "posts": posts or [import_post()], **kwargs})


def enable(db, **settings):
    db.set_config("settings", Settings(paused=False, **settings).model_dump())

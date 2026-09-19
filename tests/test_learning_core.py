import copy
from datetime import datetime, timezone
import pytest
from xautoposter.learning_core import synchronize, measurement, diagnose, DEFINITION

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)

def entry(id="a"):
    return {"id": id, "draft": {"account": "alice", "text": "本文", "topic": "test", "media_type": "text", "metric": "like_count", "horizon_hours": 24},
            "publication": {"url": "https://x.com/i/web/status/123", "published_at": "2026-09-16T00:00:00Z"}, "observations": [], "reflection": None}

def post():
    return {"id": "123", "author_id": "456", "text": "本文", "created_at": "2026-09-16T00:00:00Z",
            "snapshots": [{"observed_at": "2026-09-17T00:00:00Z", "like_count": 0}]}

def test_sync_zero_and_idempotence():
    e, count = synchronize(entry(), post(), "x_api", NOW)
    assert count == 1 and measurement(e)["value"] == 0
    again, count = synchronize(e, post(), "x_api", NOW)
    assert count == 0 and again == e
    assert not e["verified_publication"]["ownership_verified"]

@pytest.mark.parametrize("field,value", [("id","124"),("text","別の本文"),("edited",True)])
def test_mismatches_do_not_mutate(field,value):
    e, p = entry(), post(); p[field] = value
    before = copy.deepcopy(e)
    with pytest.raises(ValueError): synchronize(e,p,"x_api",NOW)
    assert e == before

def test_synthetic_rejected():
    with pytest.raises(ValueError): synchronize(entry(),post(),"synthetic",NOW)

@pytest.mark.parametrize("value", [-1, True, 1.5, float("nan")])
def test_invalid_metric_rejected(value):
    p=post(); p["snapshots"][0]["like_count"]=value
    with pytest.raises(ValueError): synchronize(entry(),p,"x_api",NOW)

def test_conflict_rolls_back_entire_copy():
    e=entry(); e["observations"]=[{"observed_at":"2026-09-17T09:00:00+09:00","like_count":10,"source":"manual"}]
    before=copy.deepcopy(e)
    with pytest.raises(ValueError): synchronize(e,post(),"x_api",NOW)
    assert e == before

def test_equal_manual_preserved_separately():
    e=entry(); e["observations"]=[{"observed_at":"2026-09-17T09:00:00+09:00","like_count":0,"source":"manual"}]
    synced,n=synchronize(e,post(),"x_api",NOW)
    assert n == 1 and len(synced["observations"])==2
    assert measurement(synced)["source"]=="x_api"

def test_late_count_does_not_fill_horizon():
    p=post(); p["snapshots"][0]["observed_at"]="2026-09-18T00:00:00Z"
    e,_=synchronize(entry(),p,"x_api",NOW)
    assert measurement(e)["value"] is None

def test_manual_and_api_peers_not_pooled():
    a,_=synchronize(entry(),post(),"x_api",NOW)
    b=entry("b"); b["observations"]=[{"source":"manual","observed_at":"2026-09-17T00:00:00Z","like_count":3}]
    assert diagnose(a,[a,b])["peers"]==[]

def test_author_ids_not_handles_define_api_peers():
    a,_=synchronize(entry(),post(),"x_api",NOW)
    p=post(); p["author_id"]="other"
    b,_=synchronize(entry("b"),p,"x_api",NOW)
    assert diagnose(a,[a,b])["peers"]==[]

def test_pinned_author_rejected():
    a,_=synchronize(entry(),post(),"x_api",NOW)
    p=post(); p["author_id"]="other"
    with pytest.raises(ValueError): synchronize(a,p,"x_api",NOW)

def test_fingerprint_stable_and_evidence_changes():
    a,_=synchronize(entry(),post(),"x_api",NOW)
    first=diagnose(a,[a]); a["version"]=100
    assert first["evidence_hash"]==diagnose(a,[a])["evidence_hash"]
    a["observations"][0]["like_count"]=1
    assert first["evidence_hash"]!=diagnose(a,[a])["evidence_hash"]


def test_partial_manual_does_not_block_complete_api_observation():
    e=entry(); e["observations"]=[{"observed_at":"2026-09-17T00:00:00Z","like_count":0,"source":"manual"}]
    p=post(); p["snapshots"][0]["reply_count"]=5
    result,count=synchronize(e,p,"x_api",NOW)
    assert count==1 and next(o for o in result["observations"] if o.get("source")=="x_api")["reply_count"]==5

def test_observation_limit_is_atomic():
    e=entry(); e["observations"]=[{"observed_at":"2026-09-16T01:00:00Z","source":"manual"} for _ in range(1000)]
    with pytest.raises(ValueError): synchronize(e,post(),"x_api",NOW)
    assert len(e["observations"])==1000

import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from xautoposter.authoring import Authoring, Apply, Generate, OpenAIWriter, conservative_length, validate_output
from xautoposter.experiments import Draft, EditDraft, Experiments
from xautoposter.learning_server import create_app


OUTPUT = {"text": "警告を増やす前に、対応が必要なものを分ける。件数だけでなく、判断にかかる時間も確かめたい。",
          "summary": "警告の数ではなく使いやすさを考える仮説です。",
          "interpretations": [{"evidence_id": "evidence", "quote": "警告の数", "interpretation": "数より判断負荷が論点かもしれません。", "alternative": "利用頻度の影響も残ります。"}],
          "changes": ["読者が検証できる問いにする"], "cautions": ["使いやすさへの効果は未検証です"]}


class FakeWriter:
    def __init__(self, result=None):
        self.result, self.calls = result or OUTPUT, 0

    async def generate(self, context):
        self.calls += 1
        return deepcopy(self.result)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("XAUTOP_LLM_ENABLED", "true")
    monkeypatch.setenv("XAUTOP_LLM_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setenv("XAUTOP_LLM_DAILY_LIMIT", "10")


def draft():
    return Draft(account="tester", topic="警告の整理", goal="判断に必要な情報を届ける", hypothesis="警告を分類すると判断しやすいか", evidence="警告の数だけでは使いやすさを判断できない")


def request(version=1):
    return Generate(version=version, consent_external_processing=True, consent_api_cost=True)


def test_meaning_and_copy_stay_separate_until_applied(db, configured):
    w = FakeWriter(); service = Authoring(db, w)
    e = service.workspace.create(draft())
    run = asyncio.run(service.generate(e['id'], request()))
    assert run['state'] == 'ready'
    assert service.workspace.get(e['id'])['draft']['text'] == ''
    applied = service.apply(e['id'], Apply(version=1, run_id=run['id'], confirmed=True))
    assert applied['draft']['text'] == OUTPUT['text']
    assert applied['publication'] is None
    assert applied['authoring']['needs_fact_review']
    assert applied['draft']['hypothesis'] == e['draft']['hypothesis']


def test_duplicate_request_and_restarted_service_do_not_charge_again(db, configured):
    w = FakeWriter(); service = Authoring(db,w); e=service.workspace.create(draft())
    a=asyncio.run(service.generate(e['id'],request()))
    b=asyncio.run(Authoring(db,w).generate(e['id'],request()))
    assert a['id'] == b['id']; assert w.calls == 1


def test_fail_closed_without_server_enable(db, monkeypatch):
    monkeypatch.delenv('XAUTOP_LLM_ENABLED',raising=False)
    w=FakeWriter();s=Authoring(db,w);e=s.workspace.create(draft())
    with pytest.raises(ValueError):asyncio.run(s.generate(e['id'],request()))
    assert w.calls==0


def test_false_consent_is_rejected():
    with pytest.raises(ValueError):Generate(version=1,consent_external_processing=False,consent_api_cost=True)


def test_untrusted_reference_and_fake_quote_rejected():
    for edit in [{'evidence_id':'unknown'},{'quote':'存在しない引用'}]:
        out=deepcopy(OUTPUT);out['interpretations'][0].update(edit)
        with pytest.raises(ValueError):validate_output(out,{'evidence':'警告の数'})


def test_rejects_long_and_placeholder_copy():
    for text in ['猫'*141, '[TODO 本文を書く]', '<script>alert(1)</script>']:
        out=deepcopy(OUTPUT);out['text']=text
        with pytest.raises(ValueError):validate_output(out,{'evidence':'警告の数'})
    assert conservative_length('https://a.co')==23
    assert conservative_length('abc猫')==5


def test_concurrent_edit_blocks_apply(db,configured):
    s=Authoring(db,FakeWriter());e=s.workspace.create(draft());r=asyncio.run(s.generate(e['id'],request()))
    s.workspace.edit(e['id'],EditDraft(**draft().model_dump(),version=1))
    with pytest.raises(ValueError):s.apply(e['id'],Apply(version=2,run_id=r['id'],confirmed=True))
    assert s.workspace.get(e['id'])['draft']['text']==''


def test_wrong_entry_cannot_apply(db,configured):
    s=Authoring(db,FakeWriter());a=s.workspace.create(draft());b=s.workspace.create(draft());r=asyncio.run(s.generate(a['id'],request()))
    with pytest.raises(ValueError):s.apply(b['id'],Apply(version=1,run_id=r['id'],confirmed=True))


def test_invalid_generation_does_not_overwrite_or_retry(db,configured):
    w=FakeWriter({'invalid':'output'});s=Authoring(db,w);e=s.workspace.create(draft())
    assert asyncio.run(s.generate(e['id'],request()))['state']=='failed'
    assert asyncio.run(s.generate(e['id'],request()))['state']=='failed'
    assert w.calls==1 and s.workspace.get(e['id'])['draft']['text']==''


def test_daily_limit_includes_failed_requests(db,configured,monkeypatch):
    monkeypatch.setenv('XAUTOP_LLM_DAILY_LIMIT','1')
    s=Authoring(db,FakeWriter({'invalid':1}));a=s.workspace.create(draft());b=s.workspace.create(draft())
    asyncio.run(s.generate(a['id'],request()))
    with pytest.raises(ValueError):asyncio.run(s.generate(b['id'],request()))


def test_adapter_has_no_tools_no_storage_and_exact_schema():
    def handler(r):
        assert str(r.url)=='https://api.openai.com/v1/responses'
        body=json.loads(r.content)
        assert body['store'] is False and 'tools' not in body
        assert body['text']['format']['strict'] is True
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(OUTPUT)}]}]})
    out=asyncio.run(OpenAIWriter('secret','model',transport=httpx.MockTransport(handler)).generate({'sources':{}}))
    assert out['text']==OUTPUT['text']


@pytest.mark.parametrize('response',[
    {'status':'incomplete'},
    {'status':'completed','output':[{'type':'message','content':[{'type':'refusal','refusal':'No'}]}]},
    {'status':'completed','output':[]},
])
def test_incomplete_and_refusals_stop(response):
    w=OpenAIWriter('secret','model',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    with pytest.raises(ValueError):asyncio.run(w.generate({}))


def test_http_boundary_and_ui_endpoint(tmp_path,configured):
    app=create_app(tmp_path,run_scheduler=False,token='')
    app.state.authoring.writer=FakeWriter()
    with TestClient(app,base_url='http://127.0.0.1') as c:
        assert c.get('/api/authoring').status_code==401
        c.headers['x-local-token']=c.get('/api/session').json()['token']
        e=c.post('/api/experiments',json=draft().model_dump()).json()
        r=c.post(f"/api/authoring/{e['id']}/generate",json=request().model_dump())
        assert r.status_code==200 and r.json()['state']=='ready'
        assert c.get('/api/authoring').json()['requests_today_utc']==1
        assert c.post(f"/api/authoring/{e['id']}/apply",json={'version':1,'run_id':r.json()['id'],'confirmed':True},headers={'Origin':'https://evil.invalid'}).status_code==403


def test_semantics_can_read_attributed_collected_replies_without_new_x_calls(db,configured):
    from xautoposter.experiments import Publication
    from conftest import bundle,import_post
    s=Authoring(db,FakeWriter());parent=s.workspace.create(Draft(**{**draft().model_dump(),'text':'元の投稿'}))
    parent=s.workspace.publish(parent['id'],Publication(version=1,url='https://x.com/tester/status/100',published_at='2026-09-10T00:00:00Z'))
    child=s.workspace.create(draft());child['parent_id']=parent['id']
    reply=import_post('101');reply.update(kind='reply',parent_id='100',text='具体的な設定方法を教えてください。')
    db.ingest(bundle(posts=[reply]),'x-live',official=True)
    ctx=s.context(child)
    assert ctx['sources']['x:101']=='具体的な設定方法を教えてください。'
    assert '最大8件' in ctx['response_scope']
    output=deepcopy(OUTPUT);output['interpretations'][0].update(evidence_id='x:101',quote='設定方法',interpretation='手順に関心がある可能性があります。')
    assert validate_output(output,ctx['sources'])['interpretations']


def test_imported_samples_are_not_official_semantic_context(db,configured):
    from xautoposter.experiments import Publication
    from conftest import bundle,import_post
    s=Authoring(db,FakeWriter());parent=s.workspace.create(Draft(**{**draft().model_dump(),'text':'元の投稿'}))
    parent=s.workspace.publish(parent['id'],Publication(version=1,url='https://x.com/tester/status/100',published_at='2026-09-10T00:00:00Z'))
    child=s.workspace.create(draft());child['parent_id']=parent['id']
    db.ingest(bundle(posts=[import_post('100')]),'x-live')
    assert not any(key.startswith('x:') for key in s.context(child)['sources'])

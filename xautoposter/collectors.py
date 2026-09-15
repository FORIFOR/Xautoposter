"""Official X GET-only adapter and bounded collection orchestration.

The origin and endpoint allowlist are code-owned. No arbitrary URL fetching, X
writes, external tools, LLM calls, or automatic HTTP retries exist here.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from .models import CollectionSource, CountBucket, ImportBundle, METRICS, Post, moment, utcnow

FIELDS = {"tweet.fields": "created_at,author_id,lang,public_metrics,attachments,referenced_tweets,conversation_id,note_tweet",
          "expansions": "attachments.media_keys", "media.fields": "type"}


class APIError(ValueError):
    pass


class XReader:
    def __init__(self, store, token="", transport=None):
        self.store = store
        self.token = token
        self.transport = transport

    async def get(self, path, params, *, post_limit=0):
        if not re.fullmatch(r"/2/tweets(?:/search/recent|/counts/recent|/\d{1,19}/quote_tweets)?", path):
            raise APIError("許可されていない読み取りエンドポイントです")
        if not self.token:
            raise APIError("X Bearer Tokenが未設定です。取り込み・合成データはキーなしで利用できます")
        cooldown = self.store.config("api_cooldown")
        if cooldown and moment(cooldown) > datetime.now(timezone.utc):
            raise APIError(f"APIのレート制限により {cooldown} まで収集を停止しています")
        s = self.store.settings()
        price = round((s.post_read_usd if post_limit else s.counts_request_usd) * 1_000_000)
        try:
            ticket = self.store.reserve(path, post_limit, price * (post_limit or 1))
        except ValueError as exc:
            raise APIError(str(exc)) from None
        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                async with client.stream("GET", "https://api.x.com" + path, params=params,
                                         headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"}) as response:
                    if response.status_code == 429:
                        until = datetime.now(timezone.utc) + timedelta(minutes=15)
                        reset = response.headers.get("x-rate-limit-reset", "")
                        retry = response.headers.get("retry-after", "")
                        try:
                            if reset:
                                until = max(datetime.now(timezone.utc) + timedelta(seconds=1), datetime.fromtimestamp(float(reset), timezone.utc))
                            elif retry:
                                until = datetime.now(timezone.utc) + timedelta(seconds=float(retry)) if retry.isdigit() else parsedate_to_datetime(retry)
                        except (ValueError, OverflowError):
                            pass
                        self.store.set_config("api_cooldown", until.isoformat())
                        raise APIError("X APIのレート制限（429）。自動再試行は行いません")
                    if response.status_code != 200:
                        labels = {401: "認証に失敗しました。Bearer Tokenを確認してください", 403: "このAPIの利用権限がありません", 402: "クレジットまたは課金設定を確認してください", 400: "検索条件・カーソルがAPIに拒否されました", 404: "対象が見つかりません"}
                        raise APIError(f"X API {response.status_code}: {labels.get(response.status_code, '読み取りに失敗しました。自動再試行は行いません')}")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 5 * 1024 * 1024:
                            raise APIError("API応答が5MBを超えました")
                    import json
                    payload = json.loads(body)
                    if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
                        raise APIError("API応答の形式が不正です")
                    if post_limit and len(payload.get("data", [])) > post_limit:
                        raise APIError("API応答が予約した取得上限を超えています")
                    if not payload.get("data") and payload.get("errors"):
                        raise APIError("対象の読み取りに失敗しました（削除・非公開・権限等）。未取得を0件成功として扱いません")
                    if "data" not in payload and not isinstance(payload.get("meta"), dict):
                        raise APIError("API応答にdata/metaがありません")
            units = len(payload.get("data", [])) if post_limit else 0
            self.store.settle(ticket, units, price * units if post_limit else price)
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            self.store.settle(ticket)  # Billing uncertainty never releases the reservation.
            if isinstance(exc, APIError):
                raise
            if isinstance(exc, httpx.TimeoutException):
                raise APIError("X APIがタイムアウトしました。費用予約を保持し、再試行はしていません") from None
            raise APIError("X APIの通信または応答の解析に失敗しました。費用予約を保持しています") from None


def normalize(payload, observed_at, topic, query, forced_parent=None):
    media = {m["media_key"]: m.get("type", "unknown") for m in payload.get("includes", {}).get("media", []) if "media_key" in m}
    posts, rejected = [], 0
    for raw in payload.get("data", []):
        try:
            references = raw.get("referenced_tweets", raw.get("referenced_posts", []))
            reply = next((r["id"] for r in references if r["type"] == "replied_to"), None)
            quote = next((r["id"] for r in references if r["type"] == "quoted"), None)
            kind, parent = ("quote", forced_parent) if forced_parent else ("reply", reply) if reply else ("quote", quote) if quote else ("original", None)
            keys = raw.get("attachments", {}).get("media_keys", [])
            formats = {media.get(key, "unknown") for key in keys}
            media_type = "unknown" if "unknown" in formats else "mixed" if len(formats) > 1 else "image" if formats == {"photo"} else "video" if formats & {"video", "animated_gif"} else "text"
            posts.append(Post.model_validate({"id": raw["id"], "author_id": raw["author_id"], "text": raw.get("note_tweet", raw.get("note_post", {})).get("text") or raw["text"],
                "created_at": raw["created_at"], "lang": raw.get("lang", "und"), "media_type": media_type,
                "topic": topic, "source_query": query, "kind": kind, "parent_id": parent,
                "snapshots": [{"observed_at": observed_at, **{k: raw.get("public_metrics", {}).get(k) for k in METRICS}}]}))
        except (ValueError, KeyError, TypeError):
            rejected += 1
    return posts, rejected


class Collector:
    def __init__(self, store, reader):
        self.store, self.reader = store, reader
        self.lock = asyncio.Lock()

    def ensure_ready(self):
        if self.store.settings().paused:
            raise APIError("収集は停止中です。収集設定から停止を解除してください")
        if not self.reader.token:
            raise APIError("X Bearer Tokenが未設定です")

    def ingest(self, posts=None, counts=None, scope="", complete=False):
        if not posts and not counts:
            return {"added_posts": 0, "added_snapshots": 0, "duplicates": 0}
        return self.store.ingest(ImportBundle(name="公式X API · 観測データ", posts=posts or [], counts=counts or [],
                                              complete=complete, scope=scope), "x-live", official=True)

    async def collect(self, source_id):
        async with self.lock:
            self.ensure_ready()
            source = self.store.source(source_id)
            config = CollectionSource.model_validate({k: v for k, v in source.items() if k not in {"id", "state"}})
            state = source["state"]
            now = datetime.now(timezone.utc)
            if state.get("next_allowed_at") and moment(state["next_allowed_at"]) > now:
                raise APIError(f"更新間隔の制限: {state['next_allowed_at']} 以降に取得できます")
            state["next_allowed_at"] = (now + timedelta(minutes=config.interval_minutes)).isoformat()
            self.store.source_state(source_id, state)
            result = {"query": config.query(), "source_name": config.name, "complete": False, "received": 0, "status": "error"}
            try:
                limit = self.store.settings().max_posts_per_request
                if config.kind == "post":
                    params = {**FIELDS, "ids": config.value}
                    payload = await self.reader.get("/2/tweets", params, post_limit=1)
                else:
                    # A page cap preserves the exact cursor + window. Advance since_id
                    # only once the original page chain is exhausted.
                    window = state.get("pending_window") or {"end_time": (now - timedelta(seconds=30)).isoformat()}
                    if "pending_window" not in state:
                        if state.get("since_id"):
                            window["since_id"] = state["since_id"]
                        else:
                            window["start_time"] = (now - timedelta(hours=24)).isoformat()
                    if state.get("next_token"):
                        window["next_token"] = state["next_token"]
                    params = {**FIELDS, **window, "query": config.query(), "max_results": limit, "sort_order": "recency"}
                    payload = await self.reader.get("/2/tweets/search/recent", params, post_limit=limit)
                    result["window"] = window
                observed = utcnow()
                posts, rejected = normalize(payload, observed, config.name, config.query())
                next_token = payload.get("meta", {}).get("next_token")
                complete = not next_token and not payload.get("errors") and rejected == 0
                result.update(self.ingest(posts, scope=f"{config.query()} / {observed} / 検索応答の標本", complete=complete))
                result.update(received=len(posts), rejected=rejected, complete=complete, next_cursor=next_token, observed_at=observed, status="complete" if complete else "partial")
                if config.kind != "post":
                    newest = payload.get("meta", {}).get("newest_id")
                    if newest and str(newest).isdigit():
                        state["pending_newest"] = str(max(int(newest), int(state.get("pending_newest") or 0)))
                    if next_token:
                        state["pending_window"] = {k: v for k, v in window.items() if k != "next_token"}
                        state["next_token"] = next_token
                    elif complete:
                        state["since_id"] = state.get("pending_newest", state.get("since_id"))
                        for k in ("pending_window", "next_token", "pending_newest"):
                            state.pop(k, None)
                    # Save search progress before the independent counts request.
                    self.store.source_state(source_id, state)
                    end = (now - timedelta(seconds=30)).replace(minute=0, second=0, microsecond=0)
                    count_payload = await self.reader.get("/2/tweets/counts/recent", {"query": config.query(), "start_time": (end - timedelta(hours=6)).isoformat(), "end_time": end.isoformat(), "granularity": "hour"})
                    count_complete = not count_payload.get("meta", {}).get("next_token") and not count_payload.get("errors")
                    buckets = [CountBucket(topic=config.name, query=config.query(), start=b["start"], end=b["end"],
                                           count=b.get("tweet_count", b.get("post_count")), observed_at=utcnow(), complete=count_complete)
                               for b in count_payload.get("data", [])]
                    self.ingest(counts=buckets, scope="公式件数API・終了済みの6時間・本文標本とは別集計", complete=count_complete)
                    result["counts_complete"] = count_complete
                    result["count_buckets"] = len(buckets)
                    if not count_complete:
                        result.update(complete=False, status="partial")
                state.update(last_observed=observed, last_status=result["status"], error=None)
            except (ValueError, KeyError, TypeError) as exc:
                result["error"] = str(exc)[:400] if isinstance(exc, APIError) else "取得データの検証または保存に失敗しました。件数・日時・設定を確認してください"
                result["status"] = "partial" if result.get("observed_at") else "error"
                result["complete"] = False
                state.update(last_status=result["status"], error=result["error"])
                if result.get("observed_at"):
                    state["last_observed"] = result["observed_at"]
            self.store.source_state(source_id, state)
            self.store.record_run(source_id, result)
            return result

    async def observe(self, ids=None, *, responses=False):
        async with self.lock:
            self.ensure_ready()
            data = self.store.data("x-live")
            candidates = [p for p in data["posts"] if p["id"] in ids] if ids else [p for p in data["posts"] if p["saved"]]
            limit = self.store.settings().max_posts_per_request
            selected = sorted(candidates, key=lambda p: p["snapshots"][-1]["observed_at"])
            if not selected:
                raise APIError("再観測する公式APIの投稿を保存してください")
            now = datetime.now(timezone.utc)
            interval = self.store.settings().tracking_interval_minutes
            if not responses:
                selected = [p for p in selected if (now - moment(p["snapshots"][-1]["observed_at"])).total_seconds() >= interval * 60]
            else:
                key = "responses:" + selected[0]["id"]
                last = self.store.config(key)
                if last and (now - moment(last)).total_seconds() < interval * 60:
                    raise APIError("返信・引用の更新間隔に達していません")
                self.store.set_config(key, utcnow())
            if not selected:
                raise APIError("再観測の更新間隔に達していません")
            selected = selected[:limit]
            result = {"source_name": "返信・引用の収集" if responses else "選択投稿の再観測", "received": 0, "complete": True, "status": "complete"}
            try:
                if responses:
                    p = selected[0]
                    tasks = [("/2/tweets/search/recent", {**FIELDS, "query": f"conversation_id:{p['id']} is:reply", "max_results": limit, "sort_order": "recency"}, None),
                             (f"/2/tweets/{p['id']}/quote_tweets", {**FIELDS, "max_results": limit}, p["id"])]
                    for path, params, parent in tasks:
                        payload = await self.reader.get(path, params, post_limit=limit)
                        posts, rejected = normalize(payload, utcnow(), p["topics"][0], params.get("query", path), parent)
                        complete = not payload.get("meta", {}).get("next_token") and not payload.get("errors") and not rejected
                        self.ingest(posts, scope="取得できた返信・引用の標本。直近検索の範囲外・未取得の返信を含みません", complete=complete)
                        result["received"] += len(posts)
                        result["complete"] &= complete
                    result["scope"] = "返信は直近7日内の会話検索、引用は最大設定件数。全反応を保証しません。"
                else:
                    payload = await self.reader.get("/2/tweets", {**FIELDS, "ids": ",".join(p["id"] for p in selected)}, post_limit=len(selected))
                    for original in selected:
                        raw = [r for r in payload.get("data", []) if r.get("id") == original["id"]]
                        if not raw:
                            result["complete"] = False
                            continue
                        posts, rejected = normalize({**payload, "data": raw}, utcnow(), original["topics"][0], "選択投稿の指標再観測")
                        self.ingest(posts, scope="選択投稿の指標再観測", complete=not rejected and not payload.get("errors"))
                        result["received"] += len(posts)
                        result["complete"] &= not rejected and not payload.get("errors")
                if not result["complete"]:
                    result["status"] = "partial"
            except (ValueError, KeyError, TypeError) as exc:
                result.update(status="partial" if result["received"] else "error", error=str(exc)[:400] if isinstance(exc, APIError) else "取得データの検証に失敗しました", complete=False)
            self.store.record_run("responses" if responses else "tracking", result)
            return result

    async def scheduler(self):
        while True:
            await asyncio.sleep(30)
            if self.store.settings().paused or not self.reader.token:
                continue
            for source in self.store.sources():
                if source["automatic"]:
                    due = source["state"].get("next_allowed_at")
                    if not due or moment(due) <= datetime.now(timezone.utc):
                        try:
                            await self.collect(source["id"])
                        except ValueError:
                            pass
            if self.store.settings().auto_track_saved:
                due = self.store.config("auto_tracking_due")
                if not due or moment(due) <= datetime.now(timezone.utc):
                    self.store.set_config("auto_tracking_due", (datetime.now(timezone.utc) + timedelta(minutes=self.store.settings().tracking_interval_minutes)).isoformat())
                    try:
                        await self.observe()
                    except ValueError:
                        pass

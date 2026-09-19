"""Own-post learning loop. Local drafts, attributed observations, human reflection.

This module has no network or publishing capability.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .models import METRICS, Snapshot, Strict, moment, post_id, utcnow
from .store import dump


class Draft(Strict):
    account: str = Field(min_length=1, max_length=16)
    topic: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=1000)
    hypothesis: str = Field(min_length=1, max_length=2000)
    text: str = Field(default="", max_length=10000)
    evidence: str = Field(default="", max_length=5000)
    change: str = Field(default="", max_length=2000)
    media_type: Literal["text", "image", "video"] = "text"
    metric: Literal["reply_count", "impression_count", "like_count", "bookmark_count", "reactions"] = "reply_count"
    horizon_hours: Literal[1, 24, 72] = 24

    @field_validator("account")
    @classmethod
    def handle(cls, value):
        value = value.removeprefix("@").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,15}", value):
            raise ValueError("投稿先は @username 形式で入力してください")
        return value


class EditDraft(Draft):
    version: int = Field(ge=1)


class Publication(Strict):
    version: int = Field(ge=1)
    url: str = Field(max_length=300)
    published_at: str

    @model_validator(mode="after")
    def validate_publication(self):
        if not self.url.startswith("https://"):
            raise ValueError("公開した投稿の https://x.com/…/status/… URLが必要です")
        self.url = f"https://x.com/i/web/status/{post_id(self.url)}"
        when = moment(self.published_at)
        if when > datetime.now(timezone.utc):
            raise ValueError("未来の日時を公開済みとして登録できません")
        self.published_at = when.isoformat(timespec="microseconds")
        return self


class Observation(Snapshot):
    version: int = Field(ge=1)
    response_notes: str = Field(default="", max_length=5000)


class Reflection(Strict):
    version: int = Field(ge=1)
    finding: str = Field(min_length=1, max_length=3000)
    evidence: str = Field(min_length=1, max_length=5000)
    alternative: str = Field(min_length=1, max_length=3000)
    next_change: str = Field(min_length=1, max_length=3000)


class Revision(Strict):
    version: int = Field(ge=1)


class SyncRequest(Strict):
    version: int = Field(ge=1)


def metric_value(observation, key):
    if key != "reactions":
        return observation.get(key)
    values = [observation.get(k) for k in METRICS[:4]]
    return sum(values) if all(v is not None for v in values) else None


def outcome(entry):
    """Use an actual observation near the preselected age; never interpolate."""
    horizon = entry["draft"]["horizon_hours"] * 60
    tolerance = min(30, max(5, horizon * .15))
    result = {"value": None, "observation": None, "age_minutes": None,
              "horizon_minutes": horizon, "tolerance_minutes": tolerance,
              "reason": "公開URLが未登録です"}
    if not entry["publication"]:
        return result
    created = moment(entry["publication"]["published_at"])
    candidates = []
    for s in entry["observations"]:
        age = (moment(s["observed_at"]) - created).total_seconds() / 60
        if abs(age - horizon) <= tolerance:
            candidates.append((abs(age - horizon), s["observed_at"], age, s))
    if not candidates:
        result["reason"] = f"投稿後{horizon / 60:g}時間（±{tolerance:g}分）の観測がなく判定不能"
        return result
    _, _, age, observation = min(candidates, key=lambda item: item[:2])
    value = metric_value(observation, entry["draft"]["metric"])
    return {**result, "value": value, "observation": observation, "age_minutes": age,
            "reason": "指定した経過時間の実測値" if value is not None else "評価する指標が欠損しているため判定不能"}


def report(entry, entries):
    result = outcome(entry)
    peers = []
    criteria = ("account", "topic", "media_type", "metric", "horizon_hours")
    if result["value"] is not None:
        for other in entries:
            if other["id"] == entry["id"] or any(other["draft"][k] != entry["draft"][k] for k in criteria):
                continue
            measured = outcome(other)
            if measured["value"] is None:
                continue
            days = abs((moment(entry["publication"]["published_at"]) - moment(other["publication"]["published_at"])).total_seconds()) / 86400
            if days > 7:
                continue
            peers.append({"id": other["id"], "value": measured["value"],
                          "observed_at": measured["observation"]["observed_at"],
                          "age_minutes": measured["age_minutes"], "url": other["publication"]["url"]})
    parent = next((p for p in peers if p["id"] == entry["parent_id"]), None)
    return {**result, "comparison_n": len(peers), "peers": peers,
            "parent_difference": result["value"] - parent["value"] if parent else None,
            "conditions": "同じアカウント・テーマ・形式・評価指標・経過時間、投稿日±7日。出典を保持した実測の標本内で比較。",
            "caution": "少数の観察比較です。投稿時刻、露出、フォロワーの変化などを統制していないため、変更の効果を断定できません。"}


class Experiments:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS experiment_history(
                    id TEXT NOT NULL REFERENCES experiments(id), version INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL, action TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(id,version));
            """)

    def all(self):
        with self.store.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT body FROM experiments ORDER BY rowid DESC")]

    def get(self, id):
        with self.store.connect() as c:
            return self._get(c, id)

    def _get(self, c, id):
        row = c.execute("SELECT body FROM experiments WHERE id=?", (id,)).fetchone()
        if not row:
            raise ValueError("発信の記録が見つかりません")
        return json.loads(row[0])

    def _write(self, c, entry, action):
        entry["version"] += 1
        entry["updated_at"] = utcnow()
        c.execute("INSERT INTO experiments VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                  (entry["id"], dump(entry)))
        c.execute("INSERT INTO experiment_history VALUES(?,?,?,?,?)",
                  (entry["id"], entry["version"], entry["updated_at"], action, dump(entry)))
        return entry

    def _new(self, draft, parent_id=None):
        return {"id": uuid.uuid4().hex, "version": 0, "draft": draft, "parent_id": parent_id,
                "publication": None, "observations": [], "reflection": None,
                "review_stale": False, "created_at": utcnow()}

    def create(self, draft):
        with self.store.connect() as c:
            return self._write(c, self._new(draft.model_dump()), "下書きを作成")

    def update(self, id, version, action, mutate):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            entry = self._get(c, id)
            if entry["version"] != version:
                raise ValueError("別の画面で更新されています。再表示してから変更してください")
            mutate(entry, c)
            return self._write(c, entry, action)

    def edit(self, id, draft):
        def mutate(entry, c):
            if entry["publication"]:
                raise ValueError("公開URL登録後の本文・仮説・評価条件は固定です。次の改善案として残してください")
            entry["draft"] = draft.model_dump(exclude={"version"})
        return self.update(id, draft.version, "下書きを編集", mutate)

    def publish(self, id, publication):
        def mutate(entry, c):
            if entry["publication"]:
                raise ValueError("この記録には公開URLが登録済みです")
            if not entry["draft"]["text"]:
                raise ValueError("公開した本文を保存してください")
            for row in c.execute("SELECT body FROM experiments"):
                other = json.loads(row[0])
                if other["publication"] and other["publication"]["url"] == publication.url:
                    raise ValueError("同じ公開URLは登録済みです")
            entry["publication"] = {**publication.model_dump(exclude={"version"}),
                                    "provenance": "user_reported", "registered_at": utcnow()}
        return self.update(id, publication.version, "公開URLを手動登録（X上の公開は未検証）", mutate)

    def observe(self, id, observation):
        def mutate(entry, c):
            if not entry["publication"]:
                raise ValueError("先に公開URL・公開日時を登録してください")
            if moment(observation.observed_at) < moment(entry["publication"]["published_at"]):
                raise ValueError("観測時刻は公開日時以降にしてください")
            if all(getattr(observation, k) is None for k in METRICS) and not observation.response_notes:
                raise ValueError("観測した指標または反応の抜粋が必要です")
            value = {**observation.model_dump(exclude={"version"}), "source": "manual", "url": entry["publication"]["url"]}
            for old in entry["observations"]:
                if old["observed_at"] == value["observed_at"]:
                    if old != value:
                        raise ValueError("同じ時刻の観測と矛盾しています。元の観測は上書きしません")
                    return
            if len(entry["observations"]) >= 1000:
                raise ValueError("この投稿の手動観測は1,000件に達しています")
            entry["observations"].append(value)
            entry["observations"].sort(key=lambda s: s["observed_at"])
            entry["review_stale"] = entry["reflection"] is not None
        return self.update(id, observation.version, "反応を手動観測", mutate)

    def sync_from_x_api(self, id, version):
        """Copy already-collected official X snapshots into the own-post loop.

        This does not make a network request. Collection remains the collector's
        responsibility, and provenance stays explicit on every copied snapshot.
        """
        def mutate(entry, c):
            if not entry["publication"]:
                raise ValueError("先に公開URL・公開日時を登録してください")
            target_id = post_id(entry["publication"]["url"])
            data = self.store.data("x-live")
            source = next((p for p in data["posts"] if p["id"] == target_id), None)
            if not source:
                raise ValueError("この投稿の公式X API観測がありません。先に収集または再観測してください")
            added = 0
            for snapshot in source["snapshots"]:
                if moment(snapshot["observed_at"]) < moment(entry["publication"]["published_at"]):
                    continue
                value = {**{k: snapshot.get(k) for k in METRICS},
                         "observed_at": snapshot["observed_at"], "response_notes": "",
                         "source": "x_api", "url": entry["publication"]["url"]}
                old = next((o for o in entry["observations"] if o["observed_at"] == value["observed_at"]), None)
                if old:
                    if any(old.get(k) != value.get(k) for k in METRICS):
                        raise ValueError("同じ時刻の手動観測と公式API観測が矛盾しています。自動上書きしません")
                    continue
                entry["observations"].append(value)
                added += 1
            if not added:
                raise ValueError("新しく同期できる公式API観測はありません")
            entry["observations"].sort(key=lambda s: s["observed_at"])
            entry["review_stale"] = entry["reflection"] is not None
            entry["last_x_api_sync_at"] = utcnow()
            entry["last_x_api_sync_count"] = added
        return self.update(id, version, "公式X APIの観測を同期", mutate)

    def reflect(self, id, reflection):
        def mutate(entry, c):
            if not entry["observations"]:
                raise ValueError("反応を観測してから振り返りを保存してください")
            entries = [json.loads(r[0]) for r in c.execute("SELECT body FROM experiments")]
            entry["reflection"] = {**reflection.model_dump(exclude={"version"}), "saved_at": utcnow(),
                                   "observations": entry["observations"][:], "report": report(entry, entries)}
            entry["review_stale"] = False
        return self.update(id, reflection.version, "振り返りと根拠を保存", mutate)

    def iterate(self, id, version):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            parent = self._get(c, id)
            if parent["version"] != version:
                raise ValueError("記録が更新されています。再表示してください")
            if not parent["reflection"] or parent["review_stale"]:
                raise ValueError("最新の観測を振り返ってから改善案を作成してください")
            draft = {**parent["draft"], "text": "", "change": parent["reflection"]["next_change"],
                     "hypothesis": parent["reflection"]["next_change"]}
            child = self._new(draft, parent["id"])
            child["parent_reflection"] = parent["reflection"]
            return self._write(c, child, "振り返りから改善案を作成")

    def history(self, id):
        self.get(id)
        with self.store.connect() as c:
            return [{**dict(r), "body": json.loads(r["body"])} for r in c.execute(
                "SELECT * FROM experiment_history WHERE id=? ORDER BY version DESC", (id,))]


def register_routes(app, store):
    workspace = Experiments(store)

    @app.get("/api/experiments")
    async def list_entries():
        entries = workspace.all()
        return [{**e, "report": report(e, entries)} for e in entries]

    @app.post("/api/experiments")
    async def create(draft: Draft):
        return workspace.create(draft)

    @app.put("/api/experiments/{id}")
    async def edit(id: str, draft: EditDraft):
        return workspace.edit(id, draft)

    @app.post("/api/experiments/{id}/publication")
    async def publication(id: str, value: Publication):
        return workspace.publish(id, value)

    @app.post("/api/experiments/{id}/observations")
    async def observe(id: str, value: Observation):
        return workspace.observe(id, value)

    @app.post("/api/experiments/{id}/sync-x-api")
    async def sync_x_api(id: str, value: SyncRequest):
        return workspace.sync_from_x_api(id, value.version)

    @app.post("/api/experiments/{id}/reflection")
    async def reflection(id: str, value: Reflection):
        return workspace.reflect(id, value)

    @app.post("/api/experiments/{id}/iterate")
    async def iterate(id: str, value: Revision):
        return workspace.iterate(id, value.version)

    @app.get("/api/experiments/{id}/history")
    async def history(id: str):
        return workspace.history(id)

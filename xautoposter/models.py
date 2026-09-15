from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

METRICS = ("like_count", "retweet_count", "reply_count", "quote_count", "bookmark_count", "impression_count")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def moment(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("日時にはタイムゾーン（Z または +09:00 など）が必要です")
    return dt.astimezone(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Metrics(Strict):
    like_count: int | None = Field(default=None, ge=0, strict=True)
    retweet_count: int | None = Field(default=None, ge=0, strict=True)
    reply_count: int | None = Field(default=None, ge=0, strict=True)
    quote_count: int | None = Field(default=None, ge=0, strict=True)
    bookmark_count: int | None = Field(default=None, ge=0, strict=True)
    impression_count: int | None = Field(default=None, ge=0, strict=True)


class Snapshot(Metrics):
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def valid_time(cls, value):
        dt = moment(value)
        if dt > datetime.now(timezone.utc):
            raise ValueError("未来の時刻を観測済みとして登録できません")
        return dt.isoformat(timespec="microseconds")


class Post(Strict):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    author_id: str = Field(min_length=1, max_length=80)
    author_name: str = Field(default="", max_length=120)
    text: str = Field(min_length=1, max_length=30000)
    created_at: str
    lang: str = Field(default="und", min_length=2, max_length=15)
    media_type: Literal["text", "image", "video", "mixed", "unknown"] = "unknown"
    topic: str = Field(min_length=1, max_length=100)
    source_query: str = Field(default="手動取り込み", max_length=512)
    kind: Literal["original", "reply", "quote"] = "original"
    parent_id: str | None = Field(default=None, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    snapshots: list[Snapshot] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def chronology(self):
        created = moment(self.created_at)
        self.created_at = created.isoformat(timespec="microseconds")
        if any(moment(s.observed_at) < created for s in self.snapshots):
            raise ValueError("観測時刻は投稿の作成時刻以降である必要があります")
        if len({s.observed_at for s in self.snapshots}) != len(self.snapshots):
            raise ValueError("1投稿内に同じ観測時刻が重複しています")
        if self.kind != "original" and not self.parent_id:
            raise ValueError("返信・引用には parent_id が必要です")
        return self


class CountBucket(Strict):
    topic: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=512)
    start: str
    end: str
    count: int = Field(ge=0, strict=True)
    observed_at: str
    complete: bool = False

    @model_validator(mode="after")
    def chronology(self):
        start, end, observed = (moment(v) for v in (self.start, self.end, self.observed_at))
        if not start < end <= observed <= datetime.now(timezone.utc):
            raise ValueError("件数の時間窓は終了済みで、開始 < 終了 <= 観測時刻である必要があります")
        self.start, self.end, self.observed_at = [v.isoformat(timespec="microseconds") for v in (start, end, observed)]
        return self


class ImportBundle(Strict):
    name: str = Field(min_length=1, max_length=100)
    provenance: Literal["imported", "synthetic"] = "imported"
    posts: list[Post] = Field(default_factory=list, max_length=5000)
    counts: list[CountBucket] = Field(default_factory=list, max_length=10000)
    complete: bool = False
    scope: str = Field(default="取り込み標本。全件性は未確認。", max_length=1000)

    @model_validator(mode="after")
    def bounded(self):
        if not self.posts and not self.counts:
            raise ValueError("投稿または件数データが必要です")
        if sum(len(p.snapshots) for p in self.posts) > 20000:
            raise ValueError("一度に取り込める観測値は20,000件までです")
        return self


class CollectionSource(Strict):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["keyword", "account", "post"] = "keyword"
    value: str = Field(min_length=1, max_length=500)
    lang: Literal["ja", "en", "all"] = "ja"
    interval_minutes: int = Field(default=60, ge=1, le=10080)
    automatic: bool = False

    @model_validator(mode="after")
    def source_value(self):
        if self.kind == "account":
            self.value = self.value.removeprefix("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", self.value):
                raise ValueError("アカウントは @username 形式で入力してください")
        if self.kind == "post":
            self.value = post_id(self.value)
        if len(self.query()) > 512:
            raise ValueError("検索条件は512文字以内にしてください")
        return self

    def query(self):
        base = f"from:{self.value}" if self.kind == "account" else self.value
        if self.kind == "post":
            return f"id:{self.value}"
        return f"({base})" + (f" lang:{self.lang}" if self.lang != "all" else "") + " -is:retweet -is:reply"


def post_id(value: str) -> str:
    if re.fullmatch(r"[0-9]{1,19}", value):
        return value
    u = urlsplit(value)
    if u.scheme != "https" or u.hostname not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} or u.username or u.password or u.port:
        raise ValueError("https://x.com/ユーザー/status/投稿ID を入力してください")
    match = re.fullmatch(r"/(?:[A-Za-z0-9_]+/status|i/web/status)/(\d{1,19})/?", u.path)
    if not match:
        raise ValueError("投稿URLの形式が正しくありません")
    return match[1]


class Settings(Strict):
    paused: bool = True
    max_posts_per_request: int = Field(default=30, ge=10, le=100)
    daily_post_limit: int = Field(default=300, ge=0, le=100000)
    daily_budget_usd: float = Field(default=2.0, ge=0, le=1000)
    monthly_budget_usd: float = Field(default=30.0, ge=0, le=10000)
    post_read_usd: float = Field(default=0.005, ge=0.000001, le=10)
    counts_request_usd: float = Field(default=0.005, ge=0.000001, le=10)
    tracking_interval_minutes: int = Field(default=60, ge=1, le=10080)
    auto_track_saved: bool = False


class SavedAnalysis(Strict):
    saved: bool = True
    note: str = Field(default="", max_length=5000)

"""Pure evidence rules. No network, credentials, model calls or X writes."""
from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from statistics import median

METRICS = ("like_count", "retweet_count", "reply_count", "quote_count", "bookmark_count", "impression_count")
DEFINITION = "x_public_metrics:v1"


def instant(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("観測日時にタイムゾーンが必要です")
    return result.astimezone(timezone.utc)


def fingerprint(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def synchronize(entry, post, provenance, now=None):
    """Validate a public post; preserve manual observations, provenance and claims.

    Matching a public post is NOT proof of account ownership. The author ID is
    pinned for later consistency checks, never interpreted as OAuth ownership.
    """
    now = now or datetime.now(timezone.utc)
    publication = entry.get("publication")
    if provenance != "x_api" or not publication:
        raise ValueError("公開URLと公式APIのデータが必要です")
    target = publication["url"].rstrip("/").split("/")[-1]
    if post["id"] != target or post.get("edited"):
        raise ValueError("投稿IDまたは本文の変更履歴を確認してください")
    normalize = lambda value: unicodedata.normalize("NFC", value.replace("\r\n", "\n")).strip()
    if normalize(post["text"]) != normalize(entry["draft"]["text"]):
        raise ValueError("保存本文と公開本文が一致しません。短縮URL・編集を含め確認が必要です")
    previous = entry.get("verified_publication")
    if previous and previous["author_id"] != post["author_id"]:
        raise ValueError("公開投稿の投稿者が前回と異なります")
    created = instant(post["created_at"])
    if created > now:
        raise ValueError("未来の投稿を公開済みとして扱えません")
    result = copy.deepcopy(entry)
    observations = result["observations"]
    added = 0
    for snapshot in post["snapshots"]:
        observed = instant(snapshot["observed_at"])
        if not created <= observed <= now:
            raise ValueError("公式観測の時刻が不正です")
        values = {key: snapshot.get(key) for key in METRICS}
        if any(v is not None and (type(v) is not int or v < 0) for v in values.values()):
            raise ValueError("公式観測の指標が不正です")
        at = observed.isoformat(timespec="microseconds")
        same = [o for o in observations if instant(o["observed_at"]) == observed]
        if any(any(o.get(k) != values[k] and (o.get("source") == "x_api" or (o.get(k) is not None and values[k] is not None)) for k in METRICS) for o in same):
            raise ValueError("同時刻の観測が矛盾しています。元データは上書きしません")
        if any(o.get("source") == "x_api" and o.get("definition") == DEFINITION for o in same):
            continue
        if len(observations) >= 1000:
            raise ValueError("観測上限1,000件に達しました。保存内容は変更していません")
        observations.append({**values, "observed_at": at, "source": "x_api", "definition": DEFINITION,
                             "url": publication["url"], "response_notes": "",
                             "evidence_id": fingerprint([target, at, DEFINITION])})
        added += 1
    observations.sort(key=lambda item: (instant(item["observed_at"]), item.get("source", "manual")))
    result["verified_publication"] = {"post_id": post["id"], "author_id": post["author_id"],
        "published_at": created.isoformat(timespec="microseconds"), "ownership_verified": False,
        "text_matched": True, "source": "x_api"}
    if added:
        result["review_stale"] = entry.get("reflection") is not None
    return result, added


def measurement(entry):
    publication = entry.get("publication")
    output = {"value": None, "source": None, "definition": None, "observation": None,
              "reason": "公開URLが未登録です"}
    if not publication:
        return output
    official = [s for s in entry["observations"] if s.get("source") == "x_api" and s.get("definition") == DEFINITION]
    # Never pool manual and API observations or silently infer an API definition.
    source = "x_api" if official and entry.get("verified_publication") else "manual"
    observations = official if source == "x_api" else [s for s in entry["observations"] if s.get("source", "manual") == "manual"]
    created = instant(entry["verified_publication"]["published_at"] if source == "x_api" else publication["published_at"])
    horizon = entry["draft"]["horizon_hours"] * 3600
    tolerance = min(1800, max(300, horizon * .15))
    eligible = [(abs((instant(s["observed_at"]) - created).total_seconds() - horizon), instant(s["observed_at"]), s)
                for s in observations if abs((instant(s["observed_at"]) - created).total_seconds() - horizon) <= tolerance]
    output.update(source=source, definition=DEFINITION if source == "x_api" else "manual:v1")
    if not eligible:
        return {**output, "reason": "指定した経過時間の観測がありません。遅い観測から逆算しません"}
    selected = min(eligible, key=lambda row: row[:2])[2]
    metric = entry["draft"]["metric"]
    values = [selected.get(k) for k in METRICS[:4]]
    value = (sum(values) if all(v is not None for v in values) else None) if metric == "reactions" else selected.get(metric)
    return {**output, "value": value, "observation": selected,
            "reason": "指定経過時間の実測" if value is not None else "評価指標が欠測です"}


def diagnose(entry, entries):
    result = measurement(entry)
    peers = []
    keys = ("account", "topic", "media_type", "metric", "horizon_hours")
    if result["value"] is not None:
        for other in entries:
            if other["id"] == entry["id"] or any(other["draft"][k] != entry["draft"][k] for k in keys):
                continue
            measured = measurement(other)
            if measured["value"] is None or (measured["source"], measured["definition"]) != (result["source"], result["definition"]):
                continue
            left = entry.get("verified_publication") if result["source"] == "x_api" else entry["publication"]
            right = other.get("verified_publication") if result["source"] == "x_api" else other["publication"]
            if result["source"] == "x_api" and left["author_id"] != right["author_id"]:
                continue
            if abs((instant(left["published_at"]) - instant(right["published_at"])).total_seconds()) > 7 * 86400:
                continue
            peers.append({"id": other["id"], "value": measured["value"], "observation": measured["observation"]})
    peers.sort(key=lambda p: p["id"])
    baseline = median(p["value"] for p in peers) if len(peers) >= 3 else None
    facts = [result["reason"]] if result["value"] is None else [f'{entry["draft"]["horizon_hours"]}時間後の{entry["draft"]["metric"]}: {result["value"]}（{result["source"]}）']
    if baseline is not None:
        facts.append(f"同条件・同出典の比較{len(peers)}件、中央値{baseline}")
    next_change = entry["draft"].get("change") or "同じテーマ・形式を維持し、冒頭で示す具体例だけを変えて検証する"
    payload = {"measurement": result, "peers": peers, "baseline": baseline, "facts": facts,
               "next_change": next_change, "uncertainty": "観察比較です。露出・時刻などの影響は分離できず、因果効果は未確認です"}
    return {**payload, "evidence_hash": fingerprint(payload), "ready": result["value"] is not None,
            "publish_allowed": False, "ownership_verified": False}

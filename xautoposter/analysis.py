from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

from .models import METRICS, moment, utcnow
from .text_analysis import hypotheses, reactions, structure

REACTION_METRICS = METRICS[:4]
METHOD = "reactions-age-match-v1"


def total(snapshot):
    values = [snapshot.get(k) for k in REACTION_METRICS]
    return None if any(v is None for v in values) else sum(values)


def age(post, snapshot):
    return (moment(snapshot["observed_at"]) - moment(post["created_at"])).total_seconds() / 60


def growth(post):
    snapshots = post["snapshots"]
    latest = snapshots[-1]
    warnings = []
    rates = []
    for left, right in zip(snapshots, snapshots[1:]):
        hours = (moment(right["observed_at"]) - moment(left["observed_at"])).total_seconds() / 3600
        a, b = total(left), total(right)
        delta = None if a is None or b is None else b - a
        decreased = any(left.get(k) is not None and right.get(k) is not None and right[k] < left[k] for k in REACTION_METRICS)
        rate = delta / hours if delta is not None and delta >= 0 and hours > 0 and not decreased else None
        rates.append({"start": left["observed_at"], "end": right["observed_at"], "hours": hours,
                      "delta": delta, "per_hour": rate, "counter_decreased": decreased})
    last = rates[-1] if rates else None
    if not last:
        warnings.append("観測不足：伸びの速度は2時点以上の実測が必要です")
    elif last["counter_decreased"]:
        warnings.append("指標が減少しています。削除・訂正等の可能性があり、伸び率は判定不能です")
    if any(latest[k] is None for k in METRICS):
        warnings.append("未取得の指標があります。欠損は0として計算していません")
    if post.get("edited"):
        warnings.append("投稿属性の更新履歴があります。以前の観測時と文章・形式が同じとは限りません")
    first_age = age(post, snapshots[0])
    stale_hours = (datetime.now(timezone.utc) - moment(latest["observed_at"])).total_seconds() / 3600
    if stale_hours > 3:
        warnings.append("最終観測から3時間以上経過しています。現在の急上昇を示すものではありません")
    accelerating = None
    if len(rates) >= 2 and all(x["per_hour"] is not None for x in rates[-2:]):
        accelerating = rates[-1]["per_hour"] > rates[-2]["per_hour"]
    return {"latest": latest, "total_reactions": total(latest), "observed_age_minutes": age(post, latest),
            "first_observed_age_minutes": first_age, "initial_speed": "初速不明（投稿直後の観測なし）" if first_age > 15 else "投稿後15分以内に観測あり（観測前の推移は不明）",
            "interval": last, "rates": rates, "velocity": last["per_hour"] if last else None,
            "accelerating": accelerating, "stale_hours": stale_hours, "warnings": warnings}


def matched(post, all_posts, topic=None):
    reference = post["snapshots"][-1]
    target_age = age(post, reference)
    tolerance = max(5, min(30, target_age * .15))
    rows = []
    if post["kind"] != "original" or post["lang"] == "und" or post["media_type"] == "unknown" or post.get("edited"):
        return rows, tolerance
    for p in all_posts:
        if p["id"] == post["id"] or p["kind"] != "original" or p["lang"] != post["lang"] or p["media_type"] != post["media_type"] or p.get("edited"):
            continue
        if topic and topic not in p["topics"]:
            continue
        if not set(p["topics"]) & set(post["topics"]):
            continue
        if abs((moment(p["created_at"]) - moment(post["created_at"])).total_seconds()) > 7 * 86400:
            continue
        candidates = [s for s in p["snapshots"] if abs(age(p, s) - target_age) <= tolerance
                      and moment(s["observed_at"]) <= moment(reference["observed_at"]) and total(s) is not None]
        if not candidates:
            continue
        s = min(candidates, key=lambda s: abs(age(p, s) - target_age))
        rows.append({"post_id": p["id"], "author_id": p["author_id"], "created_at": p["created_at"],
                     "observed_at": s["observed_at"], "age_minutes": age(p, s), "reactions": total(s),
                     "features": [f["name"] for f in structure(p["text"], p["media_type"])["features"]]})
    return rows, tolerance


def comparison(post, all_posts, topic=None):
    rows, tolerance = matched(post, all_posts, topic)
    own = [r for r in rows if r["author_id"] == post["author_id"] and moment(r["created_at"]) < moment(post["created_at"])]
    cohort, label = (own, "投稿者の過去投稿比") if len(own) >= 3 else (rows, "同条件の比較集団比")
    sufficient = len(cohort) >= (3 if cohort is own else 5)
    baseline = median([r["reactions"] for r in cohort]) if sufficient else None
    actual = total(post["snapshots"][-1])
    ratio = actual / baseline if baseline is not None and baseline >= 5 and actual is not None else None
    rank = 1 + sum(r["reactions"] > actual for r in cohort) if actual is not None and sufficient else None
    reason = "判定可能" if ratio is not None else "比較対象不足（本人3件／比較集団5件以上が必要）"
    if sufficient and baseline < 5:
        reason = "比較中央値が5反応未満のため、小さい分母による倍率を表示しません"
    if actual is None:
        reason = "反応指標に欠損があるため判定不能"
    if post["lang"] == "und" or post["media_type"] == "unknown":
        reason = "言語・投稿形式が不明なため比較不能"
    if post.get("edited"):
        reason = "本文・属性の更新があるため、同一の構成として比較できません"
    if post["kind"] != "original":
        reason = "通常比は元投稿を対象にしています。返信・引用との混合比較は行いません"
    all_rows = rows + ([{"post_id": post["id"], "author_id": post["author_id"], "reactions": actual,
                         "features": [f["name"] for f in structure(post["text"], post["media_type"])["features"]]}] if actual is not None else [])
    features, high, ordinary = [], [], []
    if len(all_rows) >= 6:
        ordered = sorted(all_rows, key=lambda r: r["reactions"])
        n = max(2, len(ordered) // 3)
        ordinary, high = ordered[:n], ordered[-n:]
        # Equal engagement is not evidence for two different outcome groups.
        if max(r["reactions"] for r in ordinary) < min(r["reactions"] for r in high):
            names = sorted({f for r in all_rows for f in r["features"]})
            for name in names:
                hi = [r["post_id"] for r in high if name in r["features"]]
                lo = [r["post_id"] for r in ordinary if name in r["features"]]
                relation = "higher" if len(hi) > len(lo) else "common" if hi and lo else "other"
                features.append({"name": name, "high_count": len(hi), "high_n": len(high), "ordinary_count": len(lo),
                                 "ordinary_n": len(ordinary), "high_ids": hi, "ordinary_ids": lo, "relation": relation})
        else:
            high, ordinary = [], []
    return {"label": label, "ratio": ratio, "median": baseline, "rank": rank, "rank_of": len(cohort) + 1,
            "sample_size": len(cohort), "own_sample_size": len(own), "reason": reason,
            "target_age_minutes": age(post, post["snapshots"][-1]), "age_tolerance_minutes": tolerance,
            "conditions": f"同じ登録テーマ・言語({post['lang']})・形式({post['media_type']})、投稿経過時間±{tolerance:.0f}分、投稿日±7日。観測時刻は対象の最終観測以前。補間なし。",
            "cohort": cohort, "features": features, "high_ids": [r["post_id"] for r in high],
            "ordinary_ids": [r["post_id"] for r in ordinary],
            "limitation": "登録テーマは収集条件名・手動タグで、内容の意味による一致は未確認です。通常群は同条件の標本内で反応下位1/3、高反応群は上位1/3。フォロワー・広告・外部流入は未統制で、因果関係は判断できません。"}


def analyze_post(data, post_id, topic=None):
    post = next((p for p in data["posts"] if p["id"] == post_id), None)
    if not post:
        raise ValueError("投稿が見つかりません")
    observed = growth(post)
    compared = comparison(post, data["posts"], topic)
    response_posts = [p for p in data["posts"] if p["parent_id"] == post_id and p["kind"] in {"reply", "quote"}]
    response = reactions(response_posts)
    structured = structure(post["text"], post["media_type"])
    interpretation = hypotheses(compared, response, structured["features"])
    return {"dataset": data["dataset"], "method_version": METHOD, "analyzed_at": utcnow(), "post": post,
            "observation": observed, "comparison": compared, "structure": structured, "reactions": response,
            **interpretation, "source_url": f"https://x.com/i/web/status/{post_id}" if post_id.isdigit() and data["dataset"]["provenance"] != "synthetic" else None}


def topic_radar(data):
    groups = defaultdict(list)
    for p in data["posts"]:
        if p["kind"] == "original":
            for topic in p["topics"]:
                groups[topic].append(p)
    for b in data["counts"]:
        groups.setdefault(b["topic"], [])
    cards = []
    for topic, posts in groups.items():
        series = defaultdict(list)
        for b in data["counts"]:
            if b["topic"] == topic:
                series[b["query"]].append(b)
        series_out = []
        for query, buckets in series.items():
            ordered = sorted(buckets, key=lambda b: b["start"])
            change = None
            pair = ordered[-2:]
            if len(pair) == 2 and all(b["complete"] for b in pair):
                a, b = pair
                durations = [(moment(v["end"]) - moment(v["start"])).total_seconds() for v in pair]
                if durations[0] == durations[1] and a["end"] == b["start"] and a["count"] > 0:
                    change = (b["count"] - a["count"]) / a["count"] * 100
            series_out.append({"query": query, "buckets": ordered, "change_percent": change})
        # Never combine queries: overlapping searches are not disjoint populations.
        main = max(series_out, key=lambda s: s["buckets"][-1]["end"], default=None)
        latest_bucket = main["buckets"][-1] if main else None
        representatives = sorted(posts, key=lambda p: (growth(p)["stale_hours"] <= 3, growth(p)["velocity"] or -1), reverse=True)[:3]
        cards.append({"topic": topic, "sample_size": len(posts), "unique_authors": len({p["author_id"] for p in posts}),
                      "series": series_out, "counts_latest": latest_bucket, "change_percent": main["change_percent"] if main else None,
                      "last_observed": max([p["snapshots"][-1]["observed_at"] for p in posts] + [b["observed_at"] for s in series_out for b in s["buckets"]], default=None),
                      "representative_ids": [p["id"] for p in representatives],
                      "query": main["query"] if main else (posts[0]["queries"][0] if posts else ""),
                      "note": "終了済みの隣接する同じ長さの窓を比較。検索式が異なる件数は合算しません。"})
    return sorted(cards, key=lambda t: (t["change_percent"] is not None, t["change_percent"] or 0), reverse=True)


def overview(data):
    rows = []
    for post in data["posts"]:
        if post["kind"] != "original":
            continue
        g = growth(post)
        c = comparison(post, data["posts"])
        rows.append({**{k: post[k] for k in ("id", "author_id", "author_name", "text", "topics", "created_at", "media_type", "saved", "lang")},
                     "latest": g["latest"], "reactions": g["total_reactions"], "velocity": g["velocity"],
                     "accelerating": g["accelerating"], "stale_hours": g["stale_hours"],
                     "ratio": c["ratio"], "baseline_label": c["label"], "comparison_n": c["sample_size"],
                     "reason": c["reason"], "structure": [f["name"] for f in structure(post["text"], post["media_type"])["features"]]})
    rows.sort(key=lambda p: (p["stale_hours"] <= 3, p["velocity"] is not None, p["velocity"] or 0), reverse=True)
    return {"dataset": data["dataset"], "topics": topic_radar(data), "posts": rows,
            "snapshot_count": sum(len(p["snapshots"]) for p in data["posts"]), "reply_count": sum(p["kind"] != "original" for p in data["posts"]),
            "last_observed": max([s["observed_at"] for p in data["posts"] for s in p["snapshots"]] + [b["observed_at"] for b in data["counts"]], default=None),
            "imports": data["imports"], "method_version": METHOD}

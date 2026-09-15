from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from .models import ImportBundle, Settings, utcnow


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS datasets(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, provenance TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS posts(
                    dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                    id TEXT NOT NULL, body TEXT NOT NULL, last_seen TEXT NOT NULL,
                    PRIMARY KEY(dataset,id));
                CREATE TABLE IF NOT EXISTS snapshots(
                    dataset TEXT NOT NULL, post_id TEXT NOT NULL, observed TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(dataset,post_id,observed),
                    FOREIGN KEY(dataset,post_id) REFERENCES posts(dataset,id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS memberships(
                    dataset TEXT NOT NULL, post_id TEXT NOT NULL, topic TEXT NOT NULL, query TEXT NOT NULL,
                    PRIMARY KEY(dataset,post_id,topic,query),
                    FOREIGN KEY(dataset,post_id) REFERENCES posts(dataset,id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS post_revisions(
                    dataset TEXT NOT NULL, post_id TEXT NOT NULL, observed TEXT NOT NULL, body TEXT NOT NULL,
                    FOREIGN KEY(dataset,post_id) REFERENCES posts(dataset,id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS counts(
                    dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                    topic TEXT NOT NULL, query TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL,
                    observed TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(dataset,topic,query,start,end,observed));
                CREATE TABLE IF NOT EXISTS saved(
                    dataset TEXT NOT NULL, post_id TEXT NOT NULL, note TEXT NOT NULL,
                    report TEXT NOT NULL, saved_at TEXT NOT NULL,
                    PRIMARY KEY(dataset,post_id),
                    FOREIGN KEY(dataset,post_id) REFERENCES posts(dataset,id) ON DELETE CASCADE);
                CREATE TABLE IF NOT EXISTS imports(
                    id TEXT PRIMARY KEY, dataset TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
                    created TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, created TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS ledger(
                    id TEXT PRIMARY KEY, created TEXT NOT NULL, endpoint TEXT NOT NULL,
                    units INTEGER NOT NULL, cost_micros INTEGER NOT NULL, state TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def config(self, key, default=None):
        with self.connect() as c:
            row = c.execute("SELECT body FROM config WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_config(self, key, value):
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (key, dump(value)))

    def settings(self):
        return Settings.model_validate(self.config("settings", {}))

    def datasets(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute("""SELECT d.*,
                (SELECT count(*) FROM posts p WHERE p.dataset=d.id) AS post_count,
                (SELECT max(observed) FROM snapshots s WHERE s.dataset=d.id) AS last_observed
                FROM datasets d ORDER BY d.created DESC""")]

    def dataset(self, dataset):
        with self.connect() as c:
            row = c.execute("SELECT * FROM datasets WHERE id=?", (dataset,)).fetchone()
            if not row:
                raise ValueError("データセットが見つかりません")
            return dict(row)

    def ingest(self, bundle: ImportBundle, dataset=None, *, official=False):
        dataset = dataset or uuid.uuid4().hex[:16]
        provenance = "x_api" if official else bundle.provenance
        now = utcnow()
        added_posts = added_snapshots = duplicates = 0
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            existing = c.execute("SELECT * FROM datasets WHERE id=?", (dataset,)).fetchone()
            if existing and existing["provenance"] != provenance:
                raise ValueError("実データ・取り込み・合成データは混在させられません")
            c.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?,?)", (dataset, bundle.name, provenance, now))
            for p in bundle.posts:
                body = p.model_dump(exclude={"snapshots", "topic", "source_query"})
                last_seen = max(s.observed_at for s in p.snapshots)
                old = c.execute("SELECT * FROM posts WHERE dataset=? AND id=?", (dataset, p.id)).fetchone()
                if old:
                    old_body = json.loads(old["body"])
                    if any(old_body[k] != body[k] for k in ("author_id", "created_at", "kind", "parent_id")):
                        raise ValueError(f"投稿 {p.id}: 同じIDの作成時刻・投稿者・返信関係が矛盾しています")
                    if dump(body) != old["body"] and last_seen >= old["last_seen"]:
                        c.execute("INSERT INTO post_revisions VALUES(?,?,?,?)", (dataset, p.id, old["last_seen"], old["body"]))
                        c.execute("UPDATE posts SET body=? WHERE dataset=? AND id=?", (dump(body), dataset, p.id))
                    c.execute("UPDATE posts SET last_seen=max(last_seen,?) WHERE dataset=? AND id=?", (last_seen, dataset, p.id))
                else:
                    c.execute("INSERT INTO posts VALUES(?,?,?,?)", (dataset, p.id, dump(body), last_seen))
                    added_posts += 1
                c.execute("INSERT OR IGNORE INTO memberships VALUES(?,?,?,?)", (dataset, p.id, p.topic, p.source_query))
                for s in p.snapshots:
                    serialized = dump(s.model_dump())
                    old_s = c.execute("SELECT body FROM snapshots WHERE dataset=? AND post_id=? AND observed=?", (dataset, p.id, s.observed_at)).fetchone()
                    if old_s:
                        if old_s[0] != serialized:
                            raise ValueError(f"投稿 {p.id}: 同じ観測時刻の指標が矛盾しています。元の記録は上書きしません")
                        duplicates += 1
                    else:
                        c.execute("INSERT INTO snapshots VALUES(?,?,?,?)", (dataset, p.id, s.observed_at, serialized))
                        added_snapshots += 1
            for bucket in bundle.counts:
                key = (dataset, bucket.topic, bucket.query, bucket.start, bucket.end, bucket.observed_at)
                old = c.execute("SELECT body FROM counts WHERE dataset=? AND topic=? AND query=? AND start=? AND end=? AND observed=?", key).fetchone()
                if old and old[0] != dump(bucket.model_dump()):
                    raise ValueError("同じ観測時刻の件数データが矛盾しています")
                c.execute("INSERT OR IGNORE INTO counts VALUES(?,?,?,?,?,?,?)", (*key, dump(bucket.model_dump())))
            result = {"dataset": dataset, "added_posts": added_posts, "added_snapshots": added_snapshots,
                      "duplicates": duplicates, "complete": bundle.complete, "scope": bundle.scope,
                      "received_posts": len(bundle.posts), "received_counts": len(bundle.counts), "created": now}
            c.execute("INSERT INTO imports VALUES(?,?,?,?)", (uuid.uuid4().hex, dataset, now, dump(result)))
        return result

    def data(self, dataset):
        meta = self.dataset(dataset)
        with self.connect() as c:
            posts = {r["id"]: {**json.loads(r["body"]), "snapshots": [], "topics": [], "queries": [], "saved": False, "note": "", "edited": False}
                     for r in c.execute("SELECT * FROM posts WHERE dataset=?", (dataset,))}
            for r in c.execute("SELECT * FROM snapshots WHERE dataset=? ORDER BY observed", (dataset,)):
                posts[r["post_id"]]["snapshots"].append(json.loads(r["body"]))
            for r in c.execute("SELECT * FROM memberships WHERE dataset=? ORDER BY topic,query", (dataset,)):
                p = posts[r["post_id"]]
                if r["topic"] not in p["topics"]:
                    p["topics"].append(r["topic"])
                if r["query"] not in p["queries"]:
                    p["queries"].append(r["query"])
            for r in c.execute("SELECT post_id FROM post_revisions WHERE dataset=?", (dataset,)):
                posts[r["post_id"]]["edited"] = True
            for r in c.execute("SELECT post_id,note,saved_at FROM saved WHERE dataset=?", (dataset,)):
                posts[r["post_id"]].update(saved=True, note=r["note"], saved_at=r["saved_at"])
            # Preserve revisions, but analyze only the latest observation of each exact bucket.
            buckets = {}
            for r in c.execute("SELECT * FROM counts WHERE dataset=? ORDER BY observed", (dataset,)):
                buckets[(r["topic"], r["query"], r["start"], r["end"])] = json.loads(r["body"])
            imports = [json.loads(r[0]) for r in c.execute("SELECT body FROM imports WHERE dataset=? ORDER BY created DESC LIMIT 20", (dataset,))]
        return {"dataset": meta, "posts": list(posts.values()), "counts": list(buckets.values()), "imports": imports}

    def save_analysis(self, dataset, post_id, saved, note, report):
        now = utcnow()
        with self.connect() as c:
            if saved:
                c.execute("INSERT OR REPLACE INTO saved VALUES(?,?,?,?,?)", (dataset, post_id, note, dump(report), now))
            else:
                c.execute("DELETE FROM saved WHERE dataset=? AND post_id=?", (dataset, post_id))
        return {"saved": saved, "note": note, "saved_at": now if saved else None}

    def saved_report(self, dataset, post_id):
        with self.connect() as c:
            row = c.execute("SELECT * FROM saved WHERE dataset=? AND post_id=?", (dataset, post_id)).fetchone()
            if not row:
                raise ValueError("保存した分析がありません")
            return {**dict(row), "report": json.loads(row["report"])}

    def delete_dataset(self, dataset):
        if dataset == "x-live":
            raise ValueError("公式APIデータの削除前に収集設定も含めた整理が必要です。P0では取り込みデータのみ削除できます")
        with self.connect() as c:
            c.execute("DELETE FROM datasets WHERE id=?", (dataset,))

    def sources(self):
        with self.connect() as c:
            return [{"id": r["id"], **json.loads(r["body"]), "state": json.loads(r["state"])} for r in c.execute("SELECT * FROM sources ORDER BY rowid")]

    def source(self, source_id):
        for source in self.sources():
            if source["id"] == source_id:
                return source
        raise ValueError("収集条件が見つかりません")

    def save_source(self, source, source_id=None):
        source_id = source_id or uuid.uuid4().hex[:16]
        with self.connect() as c:
            old = c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
            state = json.loads(old["state"]) if old else {}
            if old and any(json.loads(old["body"])[k] != source[k] for k in ("kind", "value", "lang", "name")):
                state = {}  # A changed query never reuses a previous query's cursor.
            c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?)", (source_id, dump(source), dump(state)))
        return self.source(source_id)

    def source_state(self, source_id, state):
        with self.connect() as c:
            c.execute("UPDATE sources SET state=? WHERE id=?", (dump(state), source_id))

    def delete_source(self, source_id):
        with self.connect() as c:
            c.execute("DELETE FROM sources WHERE id=?", (source_id,))

    def record_run(self, source_id, body):
        with self.connect() as c:
            c.execute("INSERT INTO runs VALUES(?,?,?,?)", (uuid.uuid4().hex, source_id, utcnow(), dump(body)))

    def runs(self):
        with self.connect() as c:
            return [{**dict(r), "body": json.loads(r["body"])} for r in c.execute("SELECT * FROM runs ORDER BY created DESC LIMIT 40")]

    def reserve(self, endpoint, max_units, cost_micros):
        now = utcnow()
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT body FROM config WHERE key='settings'").fetchone()
            s = Settings.model_validate(json.loads(r[0]) if r else {})
            if s.paused:
                raise ValueError("収集は停止中です。収集設定で停止を解除してください")
            totals = c.execute("SELECT COALESCE(sum(cost_micros),0) AS cost,COALESCE(sum(units),0) AS units FROM ledger WHERE substr(created,1,10)=?", (now[:10],)).fetchone()
            monthly = c.execute("SELECT COALESCE(sum(cost_micros),0) FROM ledger WHERE substr(created,1,7)=?", (now[:7],)).fetchone()[0]
            if totals["units"] + max_units > s.daily_post_limit:
                raise ValueError("日次の投稿読み取り上限に達するため収集を停止しました")
            if totals["cost"] + cost_micros > round(s.daily_budget_usd * 1_000_000):
                raise ValueError("日次予算の上限に達するため収集を停止しました")
            if monthly + cost_micros > round(s.monthly_budget_usd * 1_000_000):
                raise ValueError("月次予算の上限に達するため収集を停止しました")
            ticket = uuid.uuid4().hex
            c.execute("INSERT INTO ledger VALUES(?,?,?,?,?,'reserved')", (ticket, now, endpoint, max_units, cost_micros))
            return ticket

    def settle(self, ticket, units=None, cost_micros=None):
        with self.connect() as c:
            if units is None:
                c.execute("UPDATE ledger SET state='uncertain' WHERE id=?", (ticket,))
            else:
                c.execute("UPDATE ledger SET state='observed',units=?,cost_micros=? WHERE id=?", (units, cost_micros, ticket))

    def usage(self):
        now = utcnow()
        with self.connect() as c:
            today = c.execute("SELECT COALESCE(sum(cost_micros),0),COALESCE(sum(units),0) FROM ledger WHERE substr(created,1,10)=?", (now[:10],)).fetchone()
            month = c.execute("SELECT COALESCE(sum(cost_micros),0) FROM ledger WHERE substr(created,1,7)=?", (now[:7],)).fetchone()[0]
            uncertain = c.execute("SELECT count(*) FROM ledger WHERE state IN ('uncertain','reserved')").fetchone()[0]
            recent = [dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY created DESC LIMIT 15")]
        return {"day_usd": today[0] / 1_000_000, "day_posts": today[1], "month_usd": month / 1_000_000,
                "uncertain_requests": uncertain, "utc_day": now[:10], "recent": recent,
                "note": "ローカル推定額。再取得も課金対象として予約。実請求額はDeveloper Consoleで確認。"}

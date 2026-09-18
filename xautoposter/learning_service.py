"""Opt-in collection -> attributed evidence -> one unpublished next-plan draft."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone

from .experiments import Experiments
from .learning_core import diagnose, synchronize, instant
from .models import CollectionSource, utcnow
from .store import dump


class LearningLoop:
    def __init__(self, store, collector):
        self.store, self.collector = store, collector
        self.workspace = Experiments(store)
        self.lock = asyncio.Lock()

    def state(self):
        entries = self.workspace.all()
        return {"entries": [{"entry": e, "diagnosis": diagnose(e, entries)} for e in entries],
                "runtime": self.store.config("learning_runtime", {}),
                "paused": self.store.settings().paused,
                "token_configured": bool(self.collector.reader.token),
                "write_capabilities": [], "usage": self.store.usage()}

    def configure(self, id, version, enabled, auto_draft, interval_minutes):
        def mutate(entry, c):
            if not entry["publication"]:
                raise ValueError("先に公開URLを登録してください")
            source_id = f"learning-{id}"
            source = CollectionSource(name=entry["draft"]["topic"], kind="post",
                value=entry["publication"]["url"], lang="all", automatic=False,
                interval_minutes=interval_minutes).model_dump()
            c.execute("INSERT INTO sources(id,body,state) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                      (source_id, dump(source), "{}"))
            entry["learning"] = {"enabled": enabled, "auto_draft": auto_draft,
                "interval_minutes": interval_minutes, "source_id": source_id,
                "next_attempt_at": utcnow(), "error": None}
        return self.workspace.update(id, version, "改善ループの設定を更新", mutate)

    def sync(self, id, version=None):
        data = self.store.data("x-live")
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            entry = self.workspace._get(c, id)
            if version is not None and entry["version"] != version:
                raise ValueError("記録が更新されています。再表示してください")
            if not entry["publication"]:
                raise ValueError("先に公開URLを登録してください")
            target = entry["publication"]["url"].rstrip("/").split("/")[-1]
            post = next((p for p in data["posts"] if p["id"] == target), None)
            if post is None:
                raise ValueError("公式APIで対象投稿を取得してから同期してください")
            updated, added = synchronize(entry, post, data["dataset"]["provenance"])
            if not added and updated == entry:
                return entry
            updated["last_x_api_sync_at"] = utcnow()
            updated["last_x_api_sync_count"] = added
            return self.workspace._write(c, updated, "出典付きの公式観測を同期")

    def propose(self, id, version=None, *, automatic=False):
        with self.store.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            parent = self.workspace._get(c, id)
            if version is not None and parent["version"] != version:
                raise ValueError("記録が更新されています。再表示してください")
            if automatic and not (parent.get("learning", {}).get("enabled") and parent["learning"].get("auto_draft")):
                return None
            if parent.get("next_draft_id"):
                return self.workspace._get(c, parent["next_draft_id"])
            entries = [json.loads(r[0]) for r in c.execute("SELECT body FROM experiments")]
            evidence = diagnose(parent, entries)
            if not evidence["ready"]:
                if automatic:
                    return None
                raise ValueError("指定経過時間の実測がありません。観測完了後に作成できます")
            if automatic and evidence["measurement"]["source"] != "x_api":
                return None
            draft = {**parent["draft"], "text": "", "change": evidence["next_change"],
                     "hypothesis": evidence["next_change"], "evidence": "\n".join(evidence["facts"])}
            child = self.workspace._new(draft, parent["id"])
            child["parent_evidence"] = evidence
            child["creation_mode"] = "automatic_unreviewed_plan" if automatic else "evidence_plan"
            child = self.workspace._write(c, child, "実測から次の検証案を作成（本文は未執筆・未公開）")
            parent["next_draft_id"] = child["id"]
            parent["next_draft_evidence_hash"] = evidence["evidence_hash"]
            self.workspace._write(c, parent, "次の検証案との関連を保存")
            return child

    async def observe(self, id, version=None):
        async with self.lock:
            entry = self.workspace.get(id)
            if version is not None and entry["version"] != version:
                raise ValueError("記録が更新されています。再表示してください")
            setting = entry.get("learning", {})
            if not setting.get("source_id"):
                raise ValueError("先に観測設定を保存してください")
            result = await self.collector.collect(setting["source_id"])
            if result.get("status") == "error":
                raise ValueError(result.get("error", "公式APIの取得に失敗しました"))
            current = self.workspace.get(id)
            # Publication cannot change through the normal API; the version may
            # change during network IO. Do not overwrite concurrent user edits.
            updated = self.sync(id, current["version"])
            self.propose(id, automatic=True)
            return updated

    async def tick(self):
        now = datetime.now(timezone.utc)
        if self.store.settings().paused:
            self.store.set_config("learning_runtime", {"last_tick": now.isoformat(), "state": "paused"})
            return
        entries = sorted((e for e in self.workspace.all() if e.get("learning", {}).get("enabled")), key=lambda e: e["learning"]["next_attempt_at"])
        errors = 0
        deadline = time.monotonic() + 45
        for entry in entries[:8]:
            if time.monotonic() >= deadline:
                break
            settings = entry.get("learning", {})
            if not settings.get("enabled") or instant(settings["next_attempt_at"]) > now:
                continue
            # Claim next attempt before IO; source interval + global budget still
            # apply in Collector. The legacy scheduler never owns these sources.
            def claim(current, c):
                current["learning"]["next_attempt_at"] = (now + timedelta(minutes=settings["interval_minutes"])).isoformat()
            try:
                claimed = self.workspace.update(entry["id"], entry["version"], "定期観測の実行を記録", claim)
                await self.observe(claimed["id"])
                latest = self.workspace.get(entry["id"])
                report = diagnose(latest, [latest])
                completed = report["ready"] and report["measurement"]["source"] == "x_api"
                if completed or latest.get("learning", {}).get("error"):
                    def finish(e, c):
                        e["learning"]["error"] = None
                        if completed:
                            e["learning"].update(enabled=False, completed_at=utcnow())
                    self.workspace.update(latest["id"], latest["version"], "観測完了または復旧", finish)
            except ValueError as exc:
                errors += 1
                latest = self.workspace.get(entry["id"])
                message = str(exc)[:300]
                self.workspace.update(latest["id"], latest["version"], "観測を保留", lambda e,c: e["learning"].update(error=message))
        self.store.set_config("learning_runtime", {"last_tick": utcnow(), "state": "running", "errors": errors})

    async def scheduler(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                # No upstream body, token or arbitrary exception text in logs/UI.
                self.store.set_config("learning_runtime", {"last_tick": utcnow(), "state": "error", "error": "定期処理に失敗しました"})
            await asyncio.sleep(30)

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .analysis import analyze_post, overview
from .collectors import Collector, XReader
from .experiments import register_routes
from .importing import parse_import
from .models import CollectionSource, METRICS, SavedAnalysis, Settings, Strict
from .sample import sample_bundle
from .store import Store

WEB = Path(__file__).parent / "web"


class ImportRequest(Strict):
    format: str = Field(pattern="^(json|csv)$")
    name: str = Field(min_length=1, max_length=100)
    provenance: str = Field(pattern="^(synthetic|imported)$")
    text: str = Field(max_length=4 * 1024 * 1024)
    dataset: str | None = Field(default=None, max_length=80)


class SettingsRequest(Settings):
    bearer_token: str | None = Field(default=None, max_length=10000)


def create_app(data_dir: Path | None = None, *, transport=None, run_scheduler=True, token=None):
    db = Store((data_dir or Path(os.environ.get("XAUTOP_DATA_DIR", "data"))) / "radar.sqlite3")
    reader = XReader(db, token if token is not None else os.environ.get("X_BEARER_TOKEN", ""), transport)
    collector = Collector(db, reader)
    session = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(collector.scheduler()) if run_scheduler else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Xautoposter · Research", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store, app.state.collector = db, collector

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "ローカルホスト専用です"}, 403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{host}":
            return JSONResponse({"detail": "異なるオリジンからの操作は受け付けません"}, 403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "クロスサイト要求を拒否しました"}, 403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/session":
            if not secrets.compare_digest(request.headers.get("x-local-token", ""), session):
                return JSONResponse({"detail": "画面を再読み込みしてください"}, 401)
        if request.method in {"POST", "PUT", "PATCH"}:
            if len(await request.body()) > 5 * 1024 * 1024:
                return JSONResponse({"detail": "要求は5MB以下にしてください"}, 413)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)[:1500]}, 400)

    @app.get("/")
    async def home():
        return FileResponse(WEB / "index.html")

    @app.get("/api/session")
    async def bootstrap():
        return {"token": session, "version": "0.1.0", "write_capabilities": [], "text_analysis": "local-rules"}

    @app.get("/api/datasets")
    async def datasets():
        return db.datasets()

    @app.post("/api/demo")
    async def seed():
        return db.ingest(sample_bundle(), "demo")

    @app.get("/api/import-template/{format}")
    async def template(format: str):
        sample = sample_bundle().model_dump()
        if format == "json":
            return JSONResponse(sample, headers={"Content-Disposition": 'attachment; filename="synthetic-sample.json"'})
        if format != "csv":
            raise ValueError("CSVまたはJSONを指定してください")
        fields = ["id", "author_id", "author_name", "text", "created_at", "topic", "lang", "media_type", "kind", "parent_id", "source_query", "observed_at", *METRICS]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for p in sample["posts"]:
            for snapshot in p["snapshots"]:
                writer.writerow({**{k: v for k, v in p.items() if k != "snapshots"}, **snapshot})
        return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="synthetic-sample.csv"'})

    @app.post("/api/import")
    async def import_data(body: ImportRequest):
        bundle = parse_import(body.text, body.format, body.name, body.provenance)
        if body.dataset:
            db.dataset(body.dataset)
        return db.ingest(bundle, body.dataset)

    @app.get("/api/datasets/{dataset}/overview")
    async def radar(dataset: str):
        return overview(db.data(dataset))

    @app.get("/api/datasets/{dataset}/posts/{post_id}")
    async def detail(dataset: str, post_id: str):
        return analyze_post(db.data(dataset), post_id)

    @app.put("/api/datasets/{dataset}/posts/{post_id}/saved")
    async def save(dataset: str, post_id: str, body: SavedAnalysis):
        report = analyze_post(db.data(dataset), post_id)
        return db.save_analysis(dataset, post_id, body.saved, body.note, report)

    @app.get("/api/datasets/{dataset}/posts/{post_id}/saved")
    async def saved(dataset: str, post_id: str):
        return db.saved_report(dataset, post_id)

    @app.delete("/api/datasets/{dataset}")
    async def delete_dataset(dataset: str):
        db.delete_dataset(dataset)
        return {"deleted": True}

    @app.get("/api/settings")
    async def settings():
        return {"settings": db.settings().model_dump(), "token_configured": bool(reader.token),
                "usage": db.usage(), "cooldown_until": db.config("api_cooldown"), "runs": db.runs()}

    @app.put("/api/settings")
    async def update_settings(body: SettingsRequest):
        if body.bearer_token is not None:
            if any(ch.isspace() for ch in body.bearer_token):
                raise ValueError("Bearer Tokenに空白・改行を含めないでください")
            reader.token = body.bearer_token
        db.set_config("settings", body.model_dump(exclude={"bearer_token"}))
        return {"saved": True, "token_configured": bool(reader.token)}

    @app.get("/api/sources")
    async def sources():
        return db.sources()

    @app.post("/api/sources")
    async def add_source(body: CollectionSource):
        return db.save_source(body.model_dump())

    @app.put("/api/sources/{source_id}")
    async def edit_source(source_id: str, body: CollectionSource):
        db.source(source_id)
        async with collector.lock:
            return db.save_source(body.model_dump(), source_id)

    @app.delete("/api/sources/{source_id}")
    async def delete_source(source_id: str):
        async with collector.lock:
            db.delete_source(source_id)
        return {"deleted": True}

    @app.post("/api/sources/{source_id}/collect")
    async def collect(source_id: str):
        return await collector.collect(source_id)

    @app.post("/api/observe-saved")
    async def observe_saved():
        return await collector.observe()

    @app.post("/api/observe/{post_id}")
    async def observe_one(post_id: str):
        return await collector.observe([post_id])

    @app.post("/api/responses/{post_id}")
    async def responses(post_id: str):
        return await collector.observe([post_id], responses=True)

    register_routes(app, db)
    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app

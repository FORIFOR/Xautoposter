"""Compose the learning UI with the existing localhost security boundary."""
import contextlib
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi.responses import FileResponse
from pydantic import Field
from .server import create_app as research_app
from .models import Strict
from .learning_service import LearningLoop


class Version(Strict):
    version: int = Field(ge=1)


class Tracking(Version):
    enabled: bool
    auto_draft: bool = False
    interval_minutes: int = Field(default=15, ge=1, le=1440)


def create_app(*args, **kwargs):
    app = research_app(*args, **kwargs)
    loop = LearningLoop(app.state.store, app.state.collector)
    app.state.learning = loop
    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application):
            task = asyncio.create_task(loop.scheduler()) if kwargs.get("run_scheduler", True) else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
    app.router.lifespan_context = lifespan
    # Replace the early PR's unsafe sync alias with the validated implementation.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/api/experiments/{id}/sync-x-api"]

    @app.post("/api/experiments/{id}/sync-x-api")
    async def legacy_sync(id: str, value: Version):
        return loop.sync(id, value.version)

    @app.get("/research")
    async def research():
        return FileResponse(Path(__file__).parent / "web/index.html")

    @app.get("/learning")
    async def learning():
        return FileResponse(Path(__file__).parent / "web/learning.html")

    @app.get("/api/learning")
    async def state():
        return loop.state()

    @app.put("/api/learning/{id}/tracking")
    async def configure(id: str, value: Tracking):
        return loop.configure(id, **value.model_dump())

    @app.post("/api/learning/{id}/sync")
    async def sync(id: str, value: Version):
        return loop.sync(id, value.version)

    @app.post("/api/learning/{id}/observe")
    async def observe(id: str, value: Version):
        return await loop.observe(id, value.version)

    @app.post("/api/learning/{id}/propose")
    async def propose(id: str, value: Version):
        return loop.propose(id, value.version)

    return app

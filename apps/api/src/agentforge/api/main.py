"""FastAPI app entrypoint.

Boots the app, attaches lifespan, wires CORS, registers exception handlers,
and includes the router set.

Final submission registers the implemented session, file, fixture,
approval, audit, eval, and health routers.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agentforge.api.errors import register_exception_handlers
from agentforge.api.lifespan import lifespan
from agentforge.api.routers import (
    approvals,
    audit,
    evals,
    files,
    fixtures,
    health,
    sessions,
)
from agentforge.config import get_settings


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title="AgentForge API",
        version="0.1.0",
        description=(
            "Finance-team-facing app for authoring and repairing Python finance "
            "agents from Excel/CSV samples. The LLM proposes typed plans and "
            "edits; this backend deterministically validates, sandboxes, runs, "
            "and records every action."
        ),
        lifespan=lifespan,
    )

    cors_origins = [
        origin.strip()
        for origin in settings.frontend_origin.split(",")
        if origin.strip()
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    # Routers
    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(files.router)
    app.include_router(fixtures.router)
    app.include_router(approvals.router)
    app.include_router(audit.router)
    app.include_router(evals.router)

    return app


app = create_app()

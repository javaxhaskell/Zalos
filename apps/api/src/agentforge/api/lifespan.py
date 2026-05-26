"""FastAPI lifespan: init/teardown for shared resources.

Phase 1 wired the DB engine. Phase 4 added the tool-registry
singleton. Phase 5d adds the model-client singleton.

Provider selection (``LLM_PROVIDER``):

  * ``deepseek`` → :class:`DeepSeekModelClient` (OpenAI-compatible API).
  * ``ollama`` → :class:`OllamaModelClient` (local LLM; optional).
  * ``anthropic`` with a real ``sk-ant-...`` key → :class:`AnthropicModelClient`.
  * otherwise → empty :class:`FakeModelClient` (tests/evals inject their
    own scripted client; live workflow calls fail loudly on first
    ``complete()``).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agentforge.config import (
    Settings,
    deepseek_author_stage_models,
    get_settings,
    local_ollama_codegen_warning,
)
from agentforge.models import (
    AnthropicModelClient,
    DeepSeekModelClient,
    FakeModelClient,
    ModelClient,
    ModelClientError,
    OllamaModelClient,
)
from agentforge.orchestrator.runner import (
    cancel_all_run_tasks,
    recover_all_orphaned_running_sessions,
)
from agentforge.persistence.db import dispose_engine, get_session_factory, init_engine
from agentforge.persistence.migrate import ensure_migrations_applied
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.tools import build_registry

_logger = logging.getLogger("agentforge.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise shared resources on startup; tear them down on shutdown."""
    settings = get_settings()
    # DB engine — auto-apply pending Alembic revisions when schema lags.
    ensure_migrations_applied(settings.database_url)
    workspace_manager = WorkspaceManager(root=settings.workspaces_root)
    recovered = recover_all_orphaned_running_sessions(
        db_session_factory=get_session_factory(),
        workspace_manager=workspace_manager,
    )
    if recovered:
        _logger.warning(
            "recovered orphaned running sessions on startup",
            extra={"count": recovered},
        )
    # Workspaces dir is ensured by get_settings()
    # Tool registry — built once; read-only after boot. INV-2: every
    # tool the executor can dispatch lives here.
    registry = build_registry()
    app.state.tool_registry = registry
    _logger.info(
        "tool registry initialised",
        extra={"tool_count": len(registry), "tools": registry.all_names()},
    )
    # Model client — provider-specific; no scripted demo fallback.
    app.state.model_client = _build_model_client(settings)
    codegen_warning = local_ollama_codegen_warning(settings)
    if codegen_warning is not None:
        _logger.warning(
            "slow local Ollama codegen model configured",
            extra={
                "ollama_codegen_model": settings.ollama_codegen_model,
                "recommendation": codegen_warning,
            },
        )
    model_name = _primary_model_name(settings)
    _logger.info(
        "model client initialised",
        extra={
            "client_type": type(app.state.model_client).__name__,
            "llm_provider": settings.llm_provider,
            "model": model_name,
        },
    )
    try:
        yield
    finally:
        await cancel_all_run_tasks(app)
        dispose_engine()


def _primary_model_name(settings: Settings) -> str:
    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        return settings.ollama_model
    if provider == "deepseek":
        return deepseek_author_stage_models(settings)["codegen"]
    return settings.anthropic_model_primary


def _build_model_client(settings: Settings) -> ModelClient:
    """Pick the configured LLM client. Never returns a scripted demo client."""
    provider = settings.llm_provider.strip().lower()
    if provider == "deepseek":
        try:
            return DeepSeekModelClient(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=deepseek_author_stage_models(settings)["codegen"],
                timeout_seconds=settings.deepseek_timeout_seconds,
                max_retries=settings.deepseek_max_retries,
            )
        except ModelClientError as exc:
            raise RuntimeError(str(exc)) from exc

    if provider == "ollama":
        return OllamaModelClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            timeout_seconds=settings.ollama_timeout_seconds,
            num_ctx=settings.ollama_num_ctx,
        )

    key = settings.anthropic_api_key
    if key.startswith("sk-ant-"):
        return AnthropicModelClient(
            api_key=key,
            model=settings.anthropic_model_primary,
            max_retries=settings.anthropic_max_retries,
        )
    if key and not key.startswith("test-"):
        _logger.warning(
            "ANTHROPIC_API_KEY does not start with 'sk-ant-'; "
            "falling back to FakeModelClient. Set LLM_PROVIDER=deepseek "
            "for the demo API path, LLM_PROVIDER=ollama for local runs, "
            "or provide a real Anthropic key.",
        )
    return FakeModelClient(script=[])

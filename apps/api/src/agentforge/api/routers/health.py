"""Health and readiness endpoints.

- ``GET /health`` — liveness; returns 200 if the process is up.
- ``GET /health/ready`` — readiness; DB + LLM provider checks.
- ``GET /metrics`` — placeholder for the production observability extension.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentforge.api.deps import get_db_session, get_settings_dep
from agentforge.config import (
    Settings,
    deepseek_author_stage_models,
    local_ollama_codegen_warning,
)
from agentforge.models.deepseek_client import deepseek_configuration_status
from agentforge.models.ollama_client import check_ollama_ready

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "agentforge-api"
    version: str = "0.1.0"
    llm_provider: str | None = None
    llm_model: str | None = None


class ReadyResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, str]


def _health_model_name(settings: Settings) -> str:
    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        return settings.ollama_model
    if provider == "deepseek":
        return deepseek_author_stage_models(settings)["codegen"]
    return settings.anthropic_model_primary


@router.get("/health", response_model=HealthResponse)
async def health(settings: Settings = Depends(get_settings_dep)) -> HealthResponse:
    """Liveness probe — always returns 200 if the process is alive."""
    provider = settings.llm_provider.strip().lower()
    return HealthResponse(llm_provider=provider, llm_model=_health_model_name(settings))


@router.get("/health/ready")
async def ready(
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings_dep),
) -> JSONResponse:
    """Readiness probe — checks DB connectivity and the configured LLM backend."""
    checks: dict[str, str] = {}
    degraded = False

    try:
        db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = f"unreachable: {type(exc).__name__}"
        degraded = True

    provider = settings.llm_provider.strip().lower()
    if provider == "deepseek":
        ok, deepseek_checks = deepseek_configuration_status(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            strong_model=settings.deepseek_strong_model,
            stage_models=deepseek_author_stage_models(settings),
        )
        checks.update(deepseek_checks)
        if not ok:
            degraded = True
    elif provider == "ollama":
        model_checks = {
            "ollama": settings.ollama_model,
            "ollama_planning": settings.ollama_planning_model,
            "ollama_codegen": settings.ollama_codegen_model,
        }
        for check_name, model in model_checks.items():
            ok, detail = await check_ollama_ready(
                base_url=settings.ollama_base_url,
                model=model,
            )
            checks[check_name] = detail
            if not ok:
                degraded = True
        warning = local_ollama_codegen_warning(settings)
        if warning is not None:
            checks["ollama_codegen_warning"] = warning
    elif settings.anthropic_api_key.startswith("sk-ant-"):
        checks["anthropic"] = "api key configured"
    else:
        checks["llm"] = (
            "not configured — set LLM_PROVIDER=deepseek with DEEPSEEK_API_KEY, "
            "LLM_PROVIDER=ollama (and start Ollama), or provide ANTHROPIC_API_KEY"
        )
        degraded = True

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE if degraded else status.HTTP_200_OK
    ready_status: Literal["ready", "degraded"] = "degraded" if degraded else "ready"
    return JSONResponse(
        status_code=status_code,
        content=ReadyResponse(status=ready_status, checks=checks).model_dump(),
    )


@router.get("/metrics")
async def metrics() -> dict[str, str]:
    """Placeholder for the production observability extension.

    Phase 1 does not implement Prometheus exposition; documented in
    DEPLOYMENT.md as a production extension.
    """
    return {"status": "metrics endpoint not implemented in prototype"}

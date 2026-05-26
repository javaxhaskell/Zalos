"""Centralised settings loaded from environment variables.

All other modules import from here. Defaults match `.env.example`.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """AgentForge runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # --- LLM provider ---
    llm_provider: str = "deepseek"
    """``deepseek`` (default), ``ollama`` (local), or ``anthropic`` (Claude API)."""
    author_model_provider: str = ""
    """Optional alias for llm_provider, loaded from AUTHOR_MODEL_PROVIDER."""

    # --- DeepSeek (when llm_provider=deepseek) ---
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    """Legacy default fast model for Author stages when AUTHOR_*_MODEL is unset."""
    deepseek_strong_model: str = "deepseek-v4-pro"
    """Legacy stronger model for planning/review when AUTHOR_*_MODEL is unset."""
    deepseek_timeout_seconds: float = 300.0
    """Per-request timeout for DeepSeek chat completions."""
    deepseek_temperature: float = 0.1
    """Low temperature for codegen/test authoring stages."""
    deepseek_max_retries: int = 2
    """Retry count for transient DeepSeek HTTP failures (429/5xx/connect)."""

    # --- Author stage model routing ---
    author_planning_model: str = "deepseek-v4-pro"
    author_review_model: str = "deepseek-v4-pro"
    author_codegen_model: str = "deepseek-v4-flash"
    author_testgen_model: str = "deepseek-v4-flash"
    author_repair_model: str = "deepseek-v4-flash"
    """Stage-specific DeepSeek model names. Leave configurable for account availability."""

    author_enable_bank_reference_scaffold: bool = False
    """Dev-only escape hatch for the old bank reference scaffold; disabled by default."""

    author_max_repair_attempts: int = 1
    """Default execution-repair attempts before failing closed (small workflows)."""

    author_small_workflow_row_threshold: int = 25
    """Row counts at or below this use compact contract prompts and flash review."""

    author_compact_contract_prompts: bool = True
    """When true, small workflows omit repeated schema guidance and compact contracts."""

    # --- Ollama (when llm_provider=ollama) ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:14b"
    """Default Ollama tag for agent-loop and repair calls."""
    ollama_planning_model: str = "qwen2.5-coder:14b"
    """Local model for schema-heavy Author stages: contract planning/review,
    contract schema repair, and JSON repair. Override via OLLAMA_PLANNING_MODEL."""
    ollama_codegen_model: str = "qwen2.5-coder:7b"
    """Local model for Python + pytest authoring stages. Override via
    OLLAMA_CODEGEN_MODEL. Local diagnostics showed qwen2.5-coder:14b is too
    slow for Author codegen on common laptop hardware, so local codegen
    defaults to qwen2.5-coder:7b while backend validation remains deterministic."""
    ollama_codegen_temperature: float = 0.1
    """Low temperature for codegen/test authoring — reduces rambling output."""
    ollama_timeout_seconds: float = 900.0
    """Per-request timeout for all Ollama ``/api/chat`` Author model calls."""
    ollama_num_ctx: int = 12288
    """Context window for codegen/test stages."""
    ollama_planning_num_ctx: int = 8192
    """Smaller context for planning/review — faster loads on local Ollama."""

    # --- Anthropic ---
    anthropic_api_key: str = ""
    anthropic_model_primary: str = "claude-sonnet-4-6"
    """The primary model for author + repair flows. Sonnet 4.6 is the
    recommended migration target from Sonnet 4.5 per Anthropic's
    migration guide (similar latency/cost profile, adaptive thinking
    available)."""
    anthropic_model_fast: str = "claude-haiku-4-5"
    """The cheap/fast model for low-stakes calls (BP10's eval runner,
    Phase 12 telemetry summaries). Not currently wired."""
    anthropic_max_retries: int = 3
    """Passed to AsyncAnthropic. The SDK handles 408/429/5xx retries
    with exponential backoff; we don't add a hand-rolled layer."""

    # --- Database ---
    database_url: str = "sqlite:///./.workspaces/agentforge.db"

    # --- Workspace ---
    workspaces_root: Path = Field(default=Path("./.workspaces"))
    workspaces_archive_root: Path = Field(default=Path("./.workspaces-archive"))

    # --- Templates ---
    # Repo-relative default; the absolute path is resolved against the
    # process cwd at boot. Templates are retained as non-authoritative
    # reference material and test fixtures, not as final Author outputs.
    templates_root: Path = Field(default=Path("./templates"))

    # --- Bundled broken-agent fixtures (BP9) ---
    # The repair wizard surfaces a fixture picker on the InputStage so a
    # demo run does not require the user to upload a ZIP. The repair
    # `POST /sessions/{id}/load_fixture/{name}` endpoint reads from here.
    fixtures_broken_agents_root: Path = Field(default=Path("./fixtures/broken_agents"))

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    frontend_origin: str = "http://localhost:3000"

    # --- Budgets ---
    budget_tokens: int = 150_000
    budget_tool_calls: int = 40
    budget_steps_author: int = 25
    budget_steps_repair: int = 20
    budget_wall_seconds: int = 1_500
    budget_file_upload_max_bytes: int = 26_214_400  # 25 MB
    budget_files_total_max_bytes: int = 104_857_600  # 100 MB
    budget_generated_file_count: int = 20

    # --- Subprocess sandbox ---
    subprocess_timeout_script: int = 60
    subprocess_timeout_pytest: int = 120
    subprocess_output_max_bytes: int = 1_048_576  # 1 MiB

    # --- Demo identity ---
    demo_user_id: str = "user-demo"
    demo_user_email: str = "demo@agentforge.local"
    demo_user_name: str = "Demo Finance User"

    # --- Logging ---
    log_level: str = "INFO"
    log_format: str = "json"

    # --- Author blind evaluation ---
    author_blind_eval_mode: bool = False
    """When true, enforce real model contribution in blind Author runs."""

    def model_post_init(self, __context: object) -> None:
        del __context
        if self.author_model_provider.strip():
            self.llm_provider = self.author_model_provider.strip()


_settings: Settings | None = None


SLOW_LOCAL_CODEGEN_MODEL = "qwen2.5-coder:14b"
RECOMMENDED_LOCAL_CODEGEN_MODEL = "qwen2.5-coder:7b"
LOCAL_CODEGEN_MODEL_WARNING = (
    "OLLAMA_CODEGEN_MODEL=qwen2.5-coder:14b may be too slow for local Author "
    "code_generation based on measured diagnostics (~0.6 tokens/sec on this "
    "machine). Use qwen2.5-coder:7b for local codegen: "
    "`ollama pull qwen2.5-coder:7b`."
)


def mask_api_key(key: str) -> str:
    """Return a masked representation safe for logs and readiness output."""
    trimmed = key.strip()
    if not trimmed:
        return "(not set)"
    if len(trimmed) <= 8:
        return "***"
    return f"{trimmed[:4]}...{trimmed[-4:]}"


def local_ollama_codegen_warning(settings: Settings) -> str | None:
    """Return a local Ollama codegen performance warning when applicable."""
    if settings.llm_provider.strip().lower() != "ollama":
        return None
    if settings.ollama_codegen_model.strip() != SLOW_LOCAL_CODEGEN_MODEL:
        return None
    return LOCAL_CODEGEN_MODEL_WARNING


def _first_configured(*values: str) -> str:
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


def deepseek_author_stage_models(settings: Settings) -> dict[str, str]:
    """Return effective DeepSeek model names by Author stage."""
    return {
        "planning": _first_configured(
            settings.author_planning_model,
            settings.deepseek_strong_model,
            settings.deepseek_model,
        ),
        "review": _first_configured(
            settings.author_review_model,
            settings.deepseek_strong_model,
            settings.deepseek_model,
        ),
        "codegen": _first_configured(
            settings.author_codegen_model,
            settings.deepseek_model,
        ),
        "testgen": _first_configured(
            settings.author_testgen_model,
            settings.deepseek_model,
        ),
        "repair": _first_configured(
            settings.author_repair_model,
            settings.deepseek_model,
        ),
    }


def get_settings() -> Settings:
    """Return the singleton Settings instance (lazy)."""
    global _settings
    if _settings is None:
        _settings = Settings()
        # Make sure workspace dirs exist.
        _settings.workspaces_root.mkdir(parents=True, exist_ok=True)
        _settings.workspaces_archive_root.mkdir(parents=True, exist_ok=True)
    return _settings

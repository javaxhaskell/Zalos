# AgentForge — Deployment

> Local-only prototype today. Cloud Run is the recommended first production target. This document captures the deltas between local and cloud so a deployment engineer can do the swap without re-reading the codebase.

---

## What ships locally (default)

| Component | Local | Cloud (recommended) |
|---|---|---|
| API | `uv run uvicorn agentforge.api.main:app` on :8000 | Cloud Run container, autoscale 0→N |
| Web | `pnpm web:dev` on :3000 (or `pnpm web:start` after `pnpm build`) | Cloud Run container or Vercel |
| DB | SQLite at `${WORKSPACES_ROOT}/agentforge.db` (WAL) | Postgres 15+ (Cloud SQL) |
| Workspace files | `${WORKSPACES_ROOT}/<session_uuid>/` | GCS bucket mounted via `gcsfuse`, or S3 + `s3fs`, or per-pod ephemeral disk + GCS archive on finalise |
| Sandbox | subprocess + cwd-pin + timeout | Docker per session, OR hosted (E2B / Modal) |
| LLM | Ollama locally (`LLM_PROVIDER=ollama`) or Anthropic API | Anthropic API or hosted Ollama |

The `Settings` class in [`apps/api/src/agentforge/config.py`](./apps/api/src/agentforge/config.py) reads every cloud-specific value from env, so the cloud delta is config + image build, not code.

---

## Environment variables

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | no | `anthropic` | Set to `ollama` for local runs. |
| `OLLAMA_BASE_URL` | no | `http://localhost:11434` | Ollama server base URL. |
| `OLLAMA_MODEL` | no | `qwen2.5-coder:14b` | Model tag passed to `/api/chat`. Override via env; smaller models like `qwen2.5:7b` are optional fallbacks only. |
| `ANTHROPIC_API_KEY` | yes (if `anthropic`) | (empty) | Must start with `sk-ant-` when using Claude. |
| `ANTHROPIC_MODEL_PRIMARY` | no | `claude-sonnet-4-6` | The author + repair workflows. |
| `ANTHROPIC_MODEL_FAST` | no | `claude-haiku-4-5` | Reserved for eval-runner cost optimisations; not wired today. |
| `DATABASE_URL` | yes (cloud) | `sqlite:///./.workspaces/agentforge.db` | Postgres: `postgresql+psycopg://user:pass@host:5432/dbname` |
| `WORKSPACES_ROOT` | yes (cloud) | `./.workspaces` | Per-session subdirs created under here. Must be a writable, **persistent** volume. |
| `WORKSPACES_ARCHIVE_ROOT` | no | `./.workspaces-archive` | Reserved for the auto-archive-after-7d job (unimplemented). |
| `TEMPLATES_ROOT` | no | `./templates` | Bank-categoriser ships at this path. |
| `FIXTURES_BROKEN_AGENTS_ROOT` | no | `./fixtures/broken_agents` | `invoice_aging_v1` ships here. |
| `API_HOST` / `API_PORT` | no | `0.0.0.0` / `8000` | Standard FastAPI knobs. |
| `FRONTEND_ORIGIN` | yes (cloud) | `http://localhost:3000` | Comma-separated CORS allow-list. Must include the deployed web origin in cloud. |
| `BUDGET_TOKENS` | no | `150000` | Per-session token cap (INV-12). |
| `BUDGET_TOOL_CALLS` | no | `40` | Per-session tool-call cap. |
| `BUDGET_STEPS_AUTHOR` | no | `25` | Bounded loop cap for author workflow. |
| `BUDGET_STEPS_REPAIR` | no | `20` | Bounded loop cap for repair workflow. |
| `BUDGET_WALL_SECONDS` | no | `1500` | Wall-clock cap per session. |
| `BUDGET_FILE_UPLOAD_MAX_BYTES` | no | `26214400` (25 MB) | Per-file upload cap. |
| `BUDGET_FILES_TOTAL_MAX_BYTES` | no | `104857600` (100 MB) | Per-session total upload cap. |
| `SUBPROCESS_TIMEOUT_SCRIPT` | no | `60` | `run_python_script` wall-clock. |
| `SUBPROCESS_TIMEOUT_PYTEST` | no | `120` | `run_pytest` wall-clock. |
| `SUBPROCESS_OUTPUT_MAX_BYTES` | no | `1048576` (1 MiB) | stdout/stderr captured before overflow → `outputs/_logs/`. |
| `LOG_LEVEL` | no | `INFO` | `DEBUG` is verbose; `WARNING` suppresses model-call traces. |
| `LOG_FORMAT` | no | `json` | `text` is human-readable; `json` is for log aggregators. |

A starter `.env.example` ships at the repo root. Copy it to `.env` and fill `ANTHROPIC_API_KEY`.

---

## Local quickstart (recap)

```bash
cp .env.example .env       # LLM_PROVIDER=ollama by default
ollama serve
ollama pull qwen2.5-coder:14b
make setup
make migrate
make up                    # api + web concurrently
# → http://localhost:3000
```

Health checks:

- `curl http://localhost:8000/health` — liveness + configured provider/model.
- `curl http://localhost:8000/health/ready` — DB + Ollama models present (503 if Ollama is down or a configured model is missing). Local Author codegen should use `OLLAMA_CODEGEN_MODEL=qwen2.5-coder:7b`; pull it with `ollama pull qwen2.5-coder:7b`.

---

## Docker (parity option)

A `docker-compose.yml` ships at the repo root. It runs:

- `api`: builds from `apps/api/Dockerfile`, exposes 8000.
- `web`: builds from `apps/web/Dockerfile`, exposes 3000.

```bash
docker compose up --build
# → http://localhost:3000
```

The Compose path is useful for parity checks before deploying to Cloud Run. It's NOT the production posture (no sandbox isolation, no autoscale, no managed DB).

---

## Cloud Run (recommended production target)

Two services: `agentforge-api` + `agentforge-web`.

### Build the images

```bash
# from the repo root
gcloud builds submit apps/api --tag gcr.io/<project>/agentforge-api:<tag>
gcloud builds submit apps/web --tag gcr.io/<project>/agentforge-web:<tag>
```

The web Dockerfile builds the Next.js app with `pnpm build` then runs `pnpm start`; the API Dockerfile installs deps via `uv sync` then runs `uvicorn`. Both are single-stage today; tighten to multi-stage before submission to production.

### Deploy

```bash
gcloud run deploy agentforge-api \
  --image gcr.io/<project>/agentforge-api:<tag> \
  --region us-central1 \
  --platform managed \
  --min-instances 0 \
  --max-instances 5 \
  --cpu 2 --memory 2Gi \
  --concurrency 10 \
  --set-env-vars "DATABASE_URL=postgresql+psycopg://...,WORKSPACES_ROOT=/mnt/workspaces,FRONTEND_ORIGIN=https://agentforge-web-<hash>.run.app" \
  --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest" \
  --add-volume "name=workspaces,type=cloud-storage,bucket=<bucket>" \
  --add-volume-mount "volume=workspaces,mount-path=/mnt/workspaces" \
  --service-account agentforge-api@<project>.iam.gserviceaccount.com

gcloud run deploy agentforge-web \
  --image gcr.io/<project>/agentforge-web:<tag> \
  --region us-central1 \
  --platform managed \
  --min-instances 0 \
  --max-instances 5 \
  --set-env-vars "NEXT_PUBLIC_API_BASE_URL=https://agentforge-api-<hash>.run.app"
```

### Cloud SQL (Postgres)

```bash
gcloud sql instances create agentforge-prod \
  --database-version POSTGRES_15 \
  --tier db-f1-micro \
  --region us-central1

gcloud sql databases create agentforge --instance agentforge-prod

# Apply baseline migration
DATABASE_URL=postgresql+psycopg://... uv run alembic upgrade head
```

The Alembic `0001_baseline.py` migration is dialect-agnostic; the only swap from SQLite is the URL. SQLAlchemy 2.0 picks the dialect.

### Secret Manager (Anthropic key)

```bash
echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
gcloud secrets add-iam-policy-binding anthropic-api-key \
  --member="serviceAccount:agentforge-api@<project>.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

The Cloud Run `--set-secrets` flag injects the secret as an env var.

### GCS bucket for workspace files

The `WORKSPACES_ROOT` should point at a path mounted from a GCS bucket via the Cloud Run volume system. Reasons:

- Per-session workspace dirs are written + read across requests + the background runner task. Pod-local disk is wiped when the pod scales to zero.
- The archive endpoint streams `archive.zip` directly from the workspace; GCS-backed paths work transparently with `FileResponse`.
- Audit-export retrieves `events.jsonl` + `manifest.json` from the same path.

Alternative: write each artifact directly to `gs://` URIs from `WorkspaceManager.allocate` and `event_log.append`. Trade-off: a bigger code change vs. the volume-mount approach, but smaller per-call latency.

---

## Postgres swap-in (per ADR-0004)

The SQLAlchemy models in [`apps/api/src/agentforge/persistence/models.py`](./apps/api/src/agentforge/persistence/models.py) use generic SQL types that work on both SQLite and Postgres. The only SQLite-specific code is in [`db.py`](./apps/api/src/agentforge/persistence/db.py)'s `_enable_sqlite_wal` event listener, which is a no-op for non-SQLite drivers (it `try / except`s the PRAGMA setup).

For compliance-grade immutability of the event log + audit tables, add to the Postgres schema:

```sql
-- Event log immutability (server-enforced — not just convention)
REVOKE UPDATE, DELETE ON sessions, eval_runs, eval_results FROM agentforge_app;
GRANT INSERT, SELECT ON sessions, eval_runs, eval_results TO agentforge_app;

-- Append-only constraint on events log (if you move events.jsonl to a table)
CREATE TABLE events (
  id UUID PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES sessions(id),
  ts TIMESTAMPTZ NOT NULL,
  step INT NOT NULL,
  kind TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  payload JSONB NOT NULL,
  prev_event_id UUID REFERENCES events(id),
  CHECK (true)  -- placeholder; REVOKE UPDATE is the actual enforcement
);
```

The prototype uses `events.jsonl` files on disk because it's faster to iterate on contracts and the audit story is the same (append-only by convention + hash-chain via `prev_event_id`).

---

## Sandbox — production extensions

The local `SandboxRunner` in [`apps/api/src/agentforge/sandbox/runner.py`](./apps/api/src/agentforge/sandbox/runner.py) uses `subprocess.run` with `cwd=workspace`, `timeout=settings.subprocess_timeout_*`, and stdout truncation at `SUBPROCESS_OUTPUT_MAX_BYTES`. This is the **prototype-only** posture. Two production options:

### Option A — Docker per session

Wrap each `run_python_script` / `run_pytest` invocation in:

```python
docker_cmd = [
    "docker", "run",
    "--rm",
    "--network=none",                     # block egress
    f"--memory={MAX_MEMORY}",
    f"--cpus={MAX_CPUS}",
    "--read-only", "--tmpfs", "/tmp:rw",
    "-v", f"{workspace}:/workspace:rw",
    "agentforge-runtime:latest",
    *args,
]
```

Container image: Python 3.11 + the project's requirements pre-installed. Build once at deploy; cached in GCR.

### Option B — Hosted (E2B / Modal)

Replace `SandboxRunner` with an adapter that submits the command to a hosted sandbox provider. E2B's Python SDK is the closest analog to the current interface:

```python
from e2b import Sandbox

async with Sandbox.create(template="agentforge-python") as box:
    result = await box.run_python(script_path, args=args, timeout=timeout)
```

The `SandboxRunner` interface stays identical. The runner is one of the few non-Pydantic dataclass types in the codebase precisely so this swap is cheap.

---

## Healthchecks + observability

- `/health` → liveness. Returns 200 + `{"status":"ok"}` if the process is up. No DB access.
- `/health/ready` → readiness. Returns 200 if the DB is reachable (runs a `SELECT 1`), 503 otherwise.
- `/openapi.json` → full API contract.
- The audit log (per session) at `events.jsonl` is the source of truth for what happened. Append-only by convention.

Cloud Run probes should hit `/health/ready` with a 5-second grace.

For structured logging in production, set `LOG_FORMAT=json` and the structlog config in [`apps/api/src/agentforge/obs/`](./apps/api/src/agentforge/obs/) emits one JSON line per log event. Log aggregator (Cloud Logging) parses these natively.

---

## CI

`.github/workflows/ci.yml` runs four jobs on every push:

- `api` — ruff + mypy (informational) + pytest + OpenAPI snapshot diff.
- `web` — pnpm lint + typecheck + build.
- `shared` — packages/shared-schemas typecheck.
- `secrets-scan` — gitleaks. Catches an accidentally-committed `.env` or API key before it lands on main.

The OpenAPI-diff step is the load-bearing piece: any change to a route, parameter, or response model must regenerate the snapshot via `make snapshot-openapi` and commit the diff. CI fails otherwise.

---

## What this document deliberately omits

- **SSO / OIDC / RBAC.** The prototype uses a single demo cookie (`Settings.demo_user_id`). Production auth is out of scope.
- **Multi-tenancy.** Per-session workspace isolation exists; per-tenant DB partitioning does not.
- **Quotas + billing.** Anthropic-side spend is the user's; AgentForge has no billing layer.
- **Encrypted at rest.** Cloud SQL handles this. The local SQLite + filesystem do not.

These all live under the production-extension roadmap in [`docs/adr/0010-scope-exclusions.md`](./docs/adr/0010-scope-exclusions.md).

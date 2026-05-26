# Local walkthrough

Step-by-step guide to run AgentForge locally, execute both demos, and inspect audit artifacts. **Synthetic data only.**

**Recommended demo order:** Repair live first (~10 s), then Author pre-opened completed session (~3 min to walk; avoid live wait unless requested).

**Canonical green sessions:**

| Flow | Session | URL |
|------|---------|-----|
| **Repair** (`invoice_aging_v2`) | `b8317f51-384a-469a-92d3-90443853a4c5` | <http://localhost:3000/repair/b8317f51-384a-469a-92d3-90443853a4c5> |
| **Author** (Expense Exception Review) | `98942274-b4fb-4e0d-a3b8-0b8144bc01db` | <http://localhost:3000/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db> |

Full evidence summary: [`reports/final_demo_evidence_summary.md`](../reports/final_demo_evidence_summary.md).

---

## 1. Prerequisites and setup

```bash
git clone <repo>
cd Zalos
cp .env.example .env
make setup
make migrate
```

Requires Python 3.11+, `uv`, Node 20+, `pnpm`, and DeepSeek API key (default) or Ollama (optional).

## 2. Start LLM provider

**DeepSeek (default):** set `LLM_PROVIDER=deepseek` and `DEEPSEEK_API_KEY` in `.env`.

**Ollama (optional):**

```bash
ollama serve                    # if not running as a service
ollama pull qwen2.5-coder:14b   # match OLLAMA_MODEL in .env
```

Verify after starting the API:

```bash
curl http://localhost:8000/health/ready
```

Expect JSON with database + provider checks passing.

## 3. Start the app

Terminal A:

```bash
make api    # http://localhost:8000
```

Terminal B:

```bash
make web    # http://localhost:3000
```

Or `make up` if `concurrently` is installed.

## 4. Repair demo (`invoice_aging_v2`) — run live first

1. Dashboard → **Repair existing agent**.
2. **Load sample agent** (Invoice aging boundary repair) — internal name `invoice_aging_v2`.
3. Paste or confirm the problem report (bundled in the sample agent’s `data/problem_report.md` or type a short summary).
4. **Start agent**.

**Success signals:**

- **Repair complete** with evidence panel all green
- Before-fix pytest: **2 failed, 5 passed**
- After-fix pytest: **0 failed, 7 passed**
- Patch changes `days_overdue <= 31` → `days_overdue <= 30` in `working/invoice_aging_v2/agent.py`
- `workflow_completed` with `via=repair_validated_patch`

**Inspect:**

- `reports/repair_report.md` and `.json`
- `reports/agent_py.patch`
- `reports/before_fix_pytest_output.txt` and `after_fix_pytest_output.txt`
- Archive `SESSION_README.md` — repair-specific reproduction:

  ```bash
  cd working/invoice_aging_v2
  python3 -m pytest tests/ -q
  ```

## 5. Author demo (Expense Exception Review) — pre-open completed session

**Primary workflow:** Expense Exception Review (not bank categoriser).

For architecture review without a live wait, open the green completed session:

<http://localhost:3000/author/98942274-b4fb-4e0d-a3b8-0b8144bc01db>

**Green session evidence:** 190.6 s · 44,964 tokens · generated pytest **5/5** · four-tier validation **PASS** · golden **7/7 PASS** · 5/7 rows flagged (EXP-001, EXP-002, EXP-004, EXP-005, EXP-007) · 0 scaffold events · clean user-facing report paths.

**To run live (optional, ~3 min):**

1. Open <http://localhost:3000> → **Author new agent**.
2. Template picker: **Expense Exception Review** (reference sample loads prompt context only — it does **not** bypass the LLM-first Author path).
3. Upload `blind_eval_cases/expense_exception_review/input.csv` (7 rows) or use the UI reference sample.
4. Prompt: *Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.* → **Start agent**.

**Success signals:**

- Terminal banner: **AI-authored workflow validation passed**
- `workflow_completed` with `via=ai_authored_workflow_build`
- `model_called` events for `contract_planning`, `contract_review`, `code_generation`, and `test_generation`
- `generated/model_contract_plan.json`, `generated/model_contract_review.json`, `generated/agent.py`, and `generated/tests/test_agent.py` in the archive
- `reports/system_validation_report.md` — four-tier validation **Overall: PASS** (including independent golden when staged)
- `outputs/output.csv` and `outputs/exceptions.csv` downloadable

**Inspect:**

- Activity log (model calls show provider/model/token metadata)
- Expense review queue — **UI triage only**; review decisions do not mutate output files
- **Download archive (.zip)** — contains `SESSION_README.md`, `manifest.json`, `events.jsonl`, `generated/`, `uploads/`, `outputs/`, `reports/`
- Full audit: `/sessions/{id}/audit`

**Architecture note:** The model authors the output contract, agent code, and tests. Backend validators enforce universal checks, contract-specific checks, generated pytest, and independent golden comparison (`evals/golden/expense_exception_review/`). Golden is validation-only — it is not injected into codegen prompts.

## 6. Upload-your-own repair path

1. Start a new Repair session.
2. Skip the built-in sample agent; upload a ZIP via **Upload your own agent ZIP** (must include `agent.py` and `tests/`).
3. Provide a problem report and start.

Unknown bug shapes should **not** complete — expect `workflow_failed`, not fake success.

## 7. Resume behaviour

1. Copy the session URL (`/author/{uuid}` or `/repair/{uuid}`).
2. Close the tab; reopen the same URL.
3. Confirm **Progress saved** strip, restored wizard stage, artifacts, and downloads.

**Retry semantics:** Resuming a failed or incomplete session replays persisted state; a new attempt requires starting a **full rerun** (new session or explicit restart), not a partial retry.

Persistence: SQLite session row, `events.jsonl`, `manifest.json`, workspace files.

## 8. Eval runner (optional)

<http://localhost:3000/admin/evals> → **Run evals now**

Or CLI: `make eval` for bundled local scenarios. Eval scenarios (A-01 bank, R-01 v1) are CI regression paths; the live demo uses **expense + invoice_aging_v2**.

## 9. Tests (quick confidence)

```bash
make test-api
make test-template  # bank_categoriser reference-sample tests, not Author completion proof
cd apps/api && uv run pytest tests/test_repair_evidence_gate.py -q
```

## 10. Where to read more

| Topic | Doc |
|---|---|
| Requirement mapping | [`TAKE_HOME_REQUIREMENTS_COVERAGE.md`](./TAKE_HOME_REQUIREMENTS_COVERAGE.md) |
| Architecture | [`ARCHITECTURE.md`](../ARCHITECTURE.md) |
| Failure modes | [`RUNBOOK.md`](../RUNBOOK.md) |
| Final demo evidence | [`reports/final_demo_evidence_summary.md`](../reports/final_demo_evidence_summary.md) |
| In-app operator notes | <http://localhost:3000/about> |

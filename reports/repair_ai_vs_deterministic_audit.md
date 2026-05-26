# Repair Workflow Audit: AI vs Deterministic — `invoice_aging_v2`

**Date:** 2026-05-25  
**Scope:** Built-in Invoice Aging Cleanup repair demo (`invoice_aging_v2`)  
**Method:** Code review, existing audit docs, test assertions, live pytest on fixture (no code changes)

---

## Evidence table

| Area | Evidence | LLM-driven? | Deterministic? | Fixture-specific? | Honest interpretation |
|------|----------|-------------|----------------|-------------------|------------------------|
| **1. Built-in sample loading** | UI constant `BUILTIN_SAMPLE_AGENT.name = "invoice_aging_v2"` in `apps/web/app/repair/[sid]/page.tsx`. Load calls generic `POST /sessions/{id}/load_fixture/invoice_aging_v2` (`fixtures.py`). Copies `agent.py`, `problem_report.md`, `data/`, `tests/` into `working/`; stages golden to `evals/expected_output.csv`. No patch table or fix hint in loader. | No | Yes (file copy) | Yes (bundled broken agent + symptom report) | Demo content is fixture-authored, but loading is generic orchestration — not a pre-wired fix. |
| **2. Pre-repair failure reproduction** | `RepairFlow._run_pytest_capture()` runs real subprocess: `python -m pytest tests/ -v` via `SandboxRunner` (`repair_flow.py:581–606`). Parses summary with `_parse_pytest_minimal`; persists `reports/before_fix_pytest_output.txt`. Live run on fixture: **2 failed, 5 passed** (confirmed). HTTP test asserts `before["failed"] == 2`, `before["passed"] == 5` (`test_repair_run_endpoint.py:336–337`). | No | Yes (real pytest) | Yes (fixture tests designed for 2/7 split) | Counts are **not hardcoded** in orchestrator; they come from pytest. The 2/7 split is **guaranteed by fixture design**, not by faking pytest output. |
| **3. Root-cause identification** | Pipeline calls `resolve_primary_problem()` → embedded `problem_report.md` when user leaves wizard blank (`repair_problem.py:88–126`). For built-in demo, source = `built_in_sample_problem_report`. Root cause text in completed run comes from `RepairProposal.root_cause` emitted in `decision_input{kind: repair_proposal}`. Default path: `infer_proposal_from_evidence()` → `_infer_boundary_off_by_one()` regex over problem text, failing test names, pytest excerpt, and `agent.py` source (`repair_proposal.py:368–446`). Advisory `repair.info` LLM loop runs first but is non-blocking (`repair_flow.py:93–97`, `advisory_mode=True`). | Optional (advisory INFO loop + model JSON proposal) | **Yes — primary path** | Indirect (problem report + agent comments describe the v2 bug) | For the demo default path, root cause is **pattern-matched inference**, not LLM diagnosis. No `if fixture == invoice_aging_v2` branch exists (`test_repair_proposal.py::test_repair_modules_have_no_fixture_answer_table`). |
| **4. Patch generation** | `resolve_repair_proposal()` tries `request_proposal_from_model()` first, then `infer_proposal_from_evidence()` on failure (`repair_proposal.py:148–159`). Inference finds `if days_overdue <= 31:` via regex and replaces with `<= 30` using `expected_max = doc_max or boundary_day - 1` (`repair_proposal.py:401–418`). **Not** a literal hardcoded patch table. Post-apply cleanup **is** v2-specific: `_STALE_DELIBERATE_BUG`, `_STALE_BOUNDARY_COMMENT` regexes match v2 `agent.py` docstring/comment text (`repair_proposal.py:25–48`, `_refresh_repaired_agent_docstring`). | Attempted first if model client works | **Yes — default completion path** | Partial (post-patch docstring/comment cleanup tailored to v2 wording) | The `<= 31` → `<= 30` change is produced by a **generic boundary heuristic**, not a fixture-name lookup. Some **comment/docstring rewrite rules are v2-authored**. |
| **5. Model usage** | Tests for v2 demo: `FakeModelClient(script=[])` — explicitly no scripted turns (`test_repair_run_endpoint.py:290–291`, `test_repair_evidence_gate.py:103`). Empty script → advisory loop emits `llm_advisory_incomplete{reason: model_client_error}` (no `MODEL_CALLED`); proposal model call also fails → inference fallback. Asserted: `repair_proposal.source == "evidence_inference"` (`test_repair_evidence_gate.py:270`). Production: `lifespan.py` wires `DeepSeekModelClient` when `LLM_PROVIDER=deepseek`; otherwise empty `FakeModelClient`. Manifest records `model_call_count`, `llm_provider` from `MODEL_CALLED` events (`repair_flow.py:856–896`). | Optional / attempted | Yes when model unavailable or returns invalid JSON | No fixture-name gate | **Built-in demo completes without any successful model call in the tested default path.** Real DeepSeek runs would call the model, but completion still succeeds via inference if the model fails. |
| **6. Deterministic inference fallback** | Trigger: `request_proposal_from_model()` returns `None` (API error, bad JSON, or exhausted fake script). Two bug classes: `_infer_boundary_off_by_one`, `_infer_date_format_mismatch` (v1 date bug) (`repair_proposal.py:258–264`, `449–488`). Generic signals: regex on problem/tests/source/CSV sample dates — **no fixture name keys**. Fails closed for unknown shapes (`test_repair_evidence_gate.py::test_unknown_bug_shape_does_not_complete`). | No | Yes | Heuristics happen to cover v1 + v2 demo bugs | Fallback is **real, generic, and load-bearing** for the demo — not a hidden answer key keyed by fixture ID. |
| **7. Validation and evidence** | Real before/after pytest in sandbox; gates require `post_passed`, `repair_report.{md,json}`, patch artifact (`repair_flow.py:480–515`). Report fields populated from pytest summaries, failing test names, applied patch (`_write_repair_report`). **Caveat:** `business_logic_summary` is a **hardcoded invoice-aging string** in all repair reports (`repair_flow.py:99–103`, `770`), not derived from the agent at report time. | No | Yes | Hardcoded summary text is invoice-aging-specific | Validation is genuine (real pytest, real patch). Report is evidence-based **except** the static business-logic blurb. |
| **8. Custom repair path** | Same `RepairFlow`: ZIP upload → `working/` (`upload_agent_zip`), same pipeline. User problem precedence when typed (`repair_problem.py`). Unknown bugs → `workflow_failed`, not fake success (`test_repair_evidence_gate.py:281–321`). Custom agents only complete if they match one of two inference patterns (or model returns a valid proposal). v1 still tested via scripted 14-turn `FakeModelClient` E2E (`test_repair_flow_e2e.py`) — that path exercises LLM tool loop, but v2 demo does not. | Same optional LLM layer | Same deterministic backbone | Same two heuristics | Architecture is shared; **LLM is not required for completion**. Custom uploads fail honestly when evidence does not match supported bug classes. |

---

## A. Short verdict

**Category: (2) Deterministic inference — inside (4) generic repair orchestration.**

For the built-in `invoice_aging_v2` demo, the load-bearing path is a **generic, evidence-gated deterministic pipeline** that runs real pytest, infers an off-by-one boundary patch from observable signals, applies it, and re-runs pytest. The LLM advisory phase and model JSON proposal are **attempted but not required** for completion in the tested default path. The demo is **fixture-crafted** (deliberate bug, symptom report, tests) but **not fixture-scripted** in orchestrator code (no fixture-name answer table).

Rough weighting for this demo end-to-end:

| Component | Share of “what makes the demo succeed” |
|-----------|----------------------------------------|
| Generic repair orchestration (pytest, gates, patch apply, report) | ~55% |
| Deterministic inference (boundary heuristic) | ~35% |
| Fixture-specific demo content (broken agent, problem report, tests) | ~10% |
| LLM-driven repair logic | ~0% in default tested path; optional in production |

---

## B. Exact 3 sentences for operator

The built-in invoice aging repair demo completes through a deterministic evidence pipeline—real pytest before and after, a regex-derived one-line boundary patch, and gated artifacts—not through a load-bearing LLM repair loop. Model calls are advisory and proposal-first, but tests prove the demo reaches `completed` with an empty `FakeModelClient`, and event logs show `proposal_source: evidence_inference`. We should describe this as “evidence-gated automated repair with optional LLM assistance,” not “AI diagnosed and fixed the agent,” unless a production run shows successful `MODEL_CALLED` events driving the applied patch.

---

## C. What must NOT claim

- Do **not** claim the built-in demo is “fully AI-driven repair” or that the model is required for completion.
- Do **not** claim pytest failure counts are hardcoded in the orchestrator (they are parsed from real runs; the fixture is designed to produce 2/7).
- Do **not** claim the orchestrator has no fixture-specific logic at all — post-patch docstring/comment cleanup and the hardcoded `business_logic_summary` are invoice-aging-v2–authored.
- Do **not** claim custom ZIP uploads are generally AI-repairable — only two bug-class heuristics (+ optional model JSON) are supported; unknown bugs fail honestly.
- Do **not** conflate the **UI demo (v2 boundary bug)** with **eval scenario R-01 (v1 date-format bug)** — different fixtures and test counts.

---

## D. What is defensible

- **Real failure reproduction:** subprocess pytest, persisted logs, before/after counts verified on fixture and in HTTP E2E.
- **Real fix validation:** patch applied on disk, after-fix pytest must be all green to reach `completed`.
- **Honest failure mode:** unsupported bug shapes surface `workflow_failed`, not fake success.
- **No fixture-name answer table:** `repair_flow.py` / `repair_proposal.py` do not reference `invoice_aging_v2` by name (enforced by test).
- **Generic orchestration:** load_fixture, problem precedence, evidence gates, and patch validation apply to uploads and fixtures alike.
- **Optional LLM layer:** architecture supports model proposals and an advisory inspect loop; production DeepSeek wiring exists in `lifespan.py`.

---

## E. Recommended optional improvement (no implementation)

**Make repair reports and post-patch cleanup agent-agnostic:** derive `business_logic_summary` from the agent docstring (already available via `summarise_business_logic()`) instead of `_REPAIR_BUSINESS_LOGIC_SUMMARY`, and gate v2-specific docstring/comment rewrite on detected patterns rather than fixed regexes tied to one fixture’s operator notes. Separately, **surface `proposal_source` prominently in the repair UI** (`model` vs `evidence_inference`) so demo viewers can see whether the applied patch came from the LLM or deterministic fallback.

---

## Key code references

**Advisory LLM is non-blocking; deterministic pipeline owns completion:**

```93:97:apps/api/src/agentforge/orchestrator/repair_flow.py
# Cap for the advisory LLM stage. The deterministic pipeline owns the
# load-bearing repair steps; the LLM is given a short slot to inspect
# the code and propose context, but cannot block completion.
_ADVISORY_STEP_CAP = 6
_ADVISORY_WALL_SECONDS = 120
```

**Proposal resolution: model first, inference fallback:**

```148:159:apps/api/src/agentforge/orchestrator/repair_proposal.py
async def resolve_repair_proposal(
    *,
    model_client: ModelClient,
    evidence: RepairEvidence,
) -> RepairProposal | None:
    """Try model proposal first; fall back to evidence inference."""
    proposal = await request_proposal_from_model(
        model_client=model_client, evidence=evidence
    )
    if proposal is not None:
        return proposal
    return infer_proposal_from_evidence(evidence)
```

**Boundary inference (generic regex, not fixture name):**

```405:418:apps/api/src/agentforge/orchestrator/repair_proposal.py
    line_match = re.search(
        rf"^(\s*if\s+\w+\s*<=\s*{boundary_day}\s*:.*)$",
        evidence.agent_source,
        re.MULTILINE,
    )
    ...
    new_snippet = re.sub(
        rf"<=\s*{boundary_day}",
        f"<= {expected_max}",
        old_snippet,
        count=1,
    )
```

**HTTP E2E: v2 completes with empty FakeModelClient:**

```290:291:apps/api/tests/test_repair_run_endpoint.py
    # Deterministic repair pipeline — no scripted LLM turns required.
    app_client.app.state.model_client = FakeModelClient(script=[])
```

**Intentional bug in fixture (demo content, not orchestrator):**

```68:72:fixtures/broken_agents/invoice_aging_v2/agent.py
    # BOUNDARY BUG: the upper bound below should be ``<= 30``, not
    # ``<= 31``. With ``<= 31``, invoices that are exactly 31 days
    # overdue land in "1-30" instead of "31-60".
    if days_overdue <= 31:
        return ("1-30", "low")
```

---

## Supplementary notes

- Prior audits (`reports/repair_agent_architecture_audit.md`, `reports/repair_agent_sample_diagnosis.md`) align with these findings; this report focuses on the AI-vs-deterministic question.
- Live pytest on fixture (2026-05-25): `2 failed, 5 passed` — `test_boundary_31_days_in_31_60_bucket`, `test_output_matches_expected_output_csv`.
- Eval harness **R-01** still targets `invoice_aging_v1` (date-format bug); wizard demo uses **v2** (boundary bug).

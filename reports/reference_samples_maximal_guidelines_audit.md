# Reference Samples — Maximal Take-Home Brief Audit

**Scope:** Author reference sample flow, Repair built-in fixture flow, UI loaders, backend gates, golden checks, custom upload paths, README/docs.  
**Standard:** Reference samples are honest onboarding examples (sample file + prompt), not hidden presets.  
**Prior art:** [bank_fix_generality_audit.md](./bank_fix_generality_audit.md), agentforge-thesis-keeper (INV-1, INV-9, INV-10).

---

## Feature classification

| Class | Meaning |
| --- | --- |
| **A — Allowed** | Onboarding/demo helpers that do not bypass Author/Repair |
| **B — Allowed with clear labelling** | Sample-specific guidance or fixture metadata when visible and evidence-gated |
| **C — Not allowed / isolate** | Hidden bypass, backend business logic, fake provenance |

---

## Evidence table

| Area | Evidence | Allowed under maximal brief? | Risk | Fix needed |
| --- | --- | --- | --- | --- |
| Author UI reference sample card | `apps/web/app/author/[sid]/page.tsx` — radio “Reference sample”, bundled CSV auto-upload via `/api/reference-samples/bank-categoriser`, default workflow prompt with explicit rules | **A** | Low | None — UI already labels “Reference sample” |
| Author custom upload path | Same page — “Custom workflow” radio, requires user description + CSV/XLSX upload | **A** | Low | None |
| `user_message` build context | `buildAuthorRunMessage()` prefixes `Build context: … reference sample` + user prompt + uploaded paths | **A** | Low | None |
| `template_hint` recording | `POST /sessions/{id}/run` → `record_template_hint_decision` DECISION_INPUT event | **B** | Low | Document as soft UI signal, not backend preset |
| Default Author pipeline (scaffold **off**) | `author_enable_bank_reference_scaffold=false` — model contract planning/review/codegen/testgen all run | **A** | Low | None |
| Legacy bank scaffold (`AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD`) | Seeds contract, stages golden, injects BTX rows into prompts, counts scaffold stages toward completion gate | **B** (dev-only) | Medium when misused | Keep flag-gated; require `reference_sample_scaffold_mode` event before counting scaffold stages (**fixed**) |
| Demo-shaped backend prompt injection | Was: `_demo_shaped_bank_keyword_codegen_section()` + repair `demo_shaped_bank_constraints` when bundled demo detected with scaffold off | **C** | High — duplicates UI prompt as hidden backend logic | **Removed** — guidance now only in user prompt (UI) or explicit scaffold mode |
| Template code in Author prompts | Was: `bank_categoriser_template_root()` loaded `templates/bank_categoriser/*.py` as reference scaffold | **C** | High — preset agent code in model context | **Removed** — `reference_scaffold_root=None` for all default Author runs |
| Custom bank categoriser guidance | `_custom_bank_categoriser_*` when bank template + non-demo upload; logs `custom_bank_categoriser_guidance` event | **B** | Medium | Keep; event + warning string label it |
| Bundled bank validation checks | `_bank_reference_sample_validation_checks` — BTX row expectations, category counts | **B** | Low | Keep behind scaffold mode only (already gated) |
| Golden output staging | `stage_bundled_bank_reference_golden()` copies `expected_output.csv` → `evals/golden_output.csv` | **A** | Low | Keep — validation evidence only, scaffold-gated |
| Golden in codegen prompts | Contract JSON may include `golden_output_path`; codegen prompt does not embed golden contents | **A** | Low | None |
| Repair built-in fixture | `POST /load_fixture/invoice_aging_v{1,2}` from `fixtures/broken_agents/` | **A** | Low | None — satisfies “small broken fixture agent” |
| Repair custom ZIP upload | `POST /upload_agent_zip` with zip-slip/size defences | **A** | Low | None |
| Repair evidence inference | Empty FakeModelClient can infer patch from fixture metadata | **B** | Low | Documented in repair architecture audit; evidence-gated |
| `effective_author_model_stages` | Was: always counted scaffold-skipped stages | **C** when scaffold off | High — fake model stages | **Fixed** — scaffold equivalents only when `reference_sample_scaffold_mode` event present |
| README / `.env.example` | States scaffold disabled by default; sample is input/context only | **A** | Low | None |
| `reference_samples/bank_categoriser.py` | New module with detection constants (partial extraction) | **A** | Low | Optional future: move remaining `_BUNDLED_*` tables from orchestrator |

---

## Ten questions

### 1. Does selecting a reference Author sample still run file inspection, model contract, review, code, tests, execution, pytest, validation, reports/archive?

**Yes** with `AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=false` (default). File ingest/profile runs in `author_custom_build`; all four model stages run via `run_model_authoring_pipeline`; execution + generated pytest + contract validation + archive follow the same custom-build path as custom uploads.

### 2. If not, exactly what is bypassed?

**Default path:** nothing bypassed after this alignment.  
**Explicit scaffold path only (`AUTHOR_ENABLE_BANK_REFERENCE_SCAFFOLD=true`):** contract planning/review seeded from deterministic scaffold; golden staged; bundled prompt sections and BTX validation checks apply. Logged via `reference_sample_scaffold_mode` and `reference_sample_contract_scaffold_used` events.

### 3. Does the backend contain bank-specific business logic, or only prompt/example guidance?

**Default path:** no backend reclassification or output writing. User-facing rules live in the UI default prompt and `user_message`. Residual bank tables remain in the orchestrator for **scaffold-gated** dev mode and **custom bank** (non-demo upload with bank template) prompt addenda — labelled via events/warnings.  
**Removed:** demo-shaped hidden codegen/test/repair injection; template agent.py injection.

### 4. Are reference samples distinguishable from custom uploads in the UI?

**Yes.** “Reference sample” vs “Custom workflow” radios; reference sample can auto-attach bundled CSV; custom requires explicit upload.

### 5. Is there a custom Author upload path (arbitrary synthetic CSV/XLSX + prompt)?

**Yes.** Custom workflow radio — user description required, file upload required, no `template_hint` sent.

### 6. Is there a custom Repair upload path (agent folder/ZIP + problem report)?

**Yes.** `ZipUpload` on repair wizard + `POST /upload_agent_zip`; complementary to built-in fixture picker.

### 7. Does the built-in Repair fixture satisfy the brief’s broken-fixture requirement?

**Yes.** `invoice_aging_v1` / `invoice_aging_v2` under `fixtures/broken_agents/` — boundary bug, pytest failures, golden evidence, full repair pipeline.

### 8. Are golden-output checks used only as validation evidence, not authoring shortcuts?

**Yes.** Golden file is staged for Layer 5 comparison after agent execution; not injected into codegen prompts. Scaffold mode sets contract `golden_comparison_requirement=required` — still post-hoc validation.

### 9. Do reports/archives honestly record model vs fixture vs validation contributions?

**Yes.** `model_authoring_provenance`, `model_called` events, `reference_sample_scaffold_mode` / `reference_sample_contract_scaffold_used` when scaffold used, `validation_run` layer results. Completion gate no longer treats scaffold stages as model stages unless scaffold mode event recorded.

### 10. Does README/docs avoid overclaiming arbitrary universal support?

**Yes.** README states contract-driven validation, model-authored outputs, scaffold disabled by default, and synthetic samples only. Does not claim every finance workflow is pre-built.

---

## Changes applied (this alignment)

| Removed / isolated | Kept |
| --- | --- |
| Demo-shaped backend codegen/test/repair injection (default path) | Reference sample UI + bundled CSV loader |
| `templates/bank_categoriser` code injected as Author reference scaffold | Synthetic demo CSV + UI default prompt |
| Scaffold stages counted without `reference_sample_scaffold_mode` event | Broken repair fixtures + golden validation |
| — | Custom Author + Repair upload paths |
| — | Scaffold dev flag (explicit opt-in) |
| — | General Author/Repair engine + evidence-gated validation |

---

## Verdict: **GREEN** (default path)

**Rationale:** With scaffold disabled (production default), reference samples behave as ordinary user inputs: bundled file + explicit prompt through the full Author pipeline. Hidden demo-shaped injection and template-code scaffolding are removed. Golden checks and repair fixtures remain honest validation/onboarding aids. Residual bank knowledge in orchestrator is confined to explicit scaffold mode (C→B when flagged) and labelled custom-bank guidance for non-demo uploads.

**Residual AMBER (non-blocking):** `_BUNDLED_BANK_REFERENCE_*` constant tables still live in `author_llm_authoring.py` for scaffold-gated paths; partial extraction started in `reference_samples/bank_categoriser.py`. Custom-bank prompt addenda remain for bank-template + non-demo uploads.

---

## Tests added

`apps/api/tests/test_reference_sample_honesty.py` — eight tests covering stage parity, no scaffold bypass, custom Author path, repair fixture load, custom repair upload, golden-not-in-codegen, no demo output writer, provenance honesty.

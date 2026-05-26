# Final Demo Output Quality Audit

**Date:** 2026-05-25  
**Archives inspected (extract-only, unmodified):**

| Archive | Session ID | Location |
|---------|------------|----------|
| Repair | `a4d9cb75-c2c5-4f10-90a5-334d772100a7` | `/Users/arhamshuaib/Downloads/agentforge-session-a4d9cb75-c2c5-4f10-90a5-334d772100a7 (1).zip` |
| Author bank | `8a2cef12-e4cf-4215-ab30-ab05a13d2f9a` | `/Users/arhamshuaib/Downloads/agentforge-session-8a2cef12-e4cf-4215-ab30-ab05a13d2f9a.zip` |

No copies of these session IDs were found under `.workspaces/` (only unrelated `.workspaces-deepseek-rerun/ce21e7e5-…`).

---

| Demo | Area | Evidence | Root cause class | General fix needed? | Fix target | Status |
|------|------|----------|------------------|---------------------|------------|--------|
| Repair | A. Provenance consistency | `manifest.json`: `model_call_count=1`, `model_contributed=false`, `completion.tokens_used=0` while `budget.tokens_used=4830` and `events.jsonl` has `MODEL_CALLED` with `total_tokens=4830` | Repair completion metadata omits token aggregation and mis-sets `model_contributed` when the model supplies the patch proposal | Yes | `repair_proposal.repair_provenance_from_events`, `repair_flow._build_repair_completion_metadata` | Fixed |
| Repair | B. Remaining-risks stale comment | `repair_report.json` / `.md`: "The comment documenting the bug becomes stale…" but patch only changed `if days_overdue <= 31:` → `<= 30` (no comment edit) | Model proposal risk copied verbatim without evidence check | Yes | `repair_proposal.derive_repair_remaining_risks` | Fixed |
| Repair | C. Proposal source clarity | Report shows problem source (`built_in_sample_problem_report`) but not that applied patch came from `proposal_source: model` (event + JSON `patch_proposal.source`) | Markdown renderer lacks a dedicated patch-proposal section | Yes | `repair_flow._render_repair_markdown` | Fixed |
| Author bank | D. Pytest `REQUIRED_ARTIFACTS` vs contract | `generated/tests/test_agent.py` uses `CONTRACT.get("required_artifacts", [])` but `author_output_contract.json` has no `required_artifacts` key — artifact test is a no-op | Test-generation prompt assumes a contract field that is not persisted | Yes | `author_llm_authoring._test_generation_prompt` guidance | Fixed |
| Both | E. Absolute path leakage | `manifest.json` `workspace_path`: `/Users/arhamshuaib/Desktop/Zalos/.workspaces/…` in exported archives | Manifest stores absolute workspace path; archive export does not redact | Yes | `persistence/user_facing.sanitize_manifest_for_export`, archive build | Fixed |
| Author bank | F. Budget / provenance counters | `SESSION_README.md`: `Tokens used: 34216` but `Budget on completion: tokens=0`; `manifest.budget` all zeros while `completion.tokens_used=34216` | `build_archive` writes README before runner finalizes budget; README reads stale manifest budget | Yes | `user_facing.effective_budget_display`, sync budget before archive | Fixed |
| Author bank | G. Output quality (classification) | `outputs/output.csv`: 7/10 rows `Uncategorised` (70%); warning already in manifest + README | Model-authored rules under-fit demo bank patterns — not a validation bug | No (warning only) | Existing `_semantic_output_warnings` | Already present |
| Both | H. Final demo recommendation | See below | Presentation / archive selection | N/A | Demo packaging | Recommendation |

---

## Issue details

### A — Repair provenance

Events show one successful DeepSeek `MODEL_CALLED` (4830 tokens). Completion block records the call count but leaves `tokens_used=0` and `model_contributed=false`, while manifest budget correctly shows 4830. Consumers reading completion alone get a contradictory story.

### B — Remaining risks

The model-generated risk references a stale **comment**, but the applied diff is a one-line numeric boundary change. This is hallucinated residual risk, not evidence-backed.

### C — Proposal source

Event `patch_applied.proposal_source=model` and JSON `patch_proposal.source=model` exist, but the markdown report never states that the patch proposal came from the LLM (vs deterministic inference). Demo viewers cannot tell which path was load-bearing.

### D — Generated pytest artifacts

The generated test defines `REQUIRED_ARTIFACTS = CONTRACT.get("required_artifacts", [])`. The persisted contract schema lists deliverables via `requested_deliverables` / `row_level_output_file`, not `required_artifacts`. The required-artifact test passes vacuously.

### E — Path leakage

Exported `manifest.json` embeds the developer's absolute filesystem path. Unprofessional in portable archives and breaks portability narratives.

### F — Budget consistency

Author completion records 34216 tokens and four model calls, but manifest budget counters remain zero when the archive README is generated. README therefore shows contradictory token lines.

### G — Bank categorisation quality

70% uncategorised is a legitimate quality concern for demo storytelling. System already emits a non-gating warning (`High uncategorised ratio…`). No bank-specific hardcoding applied; classification quality is a model/rules issue, not a validation false-pass.

---

## H — Final demo recommendation

| Role | Recommendation | Rationale |
|------|----------------|-----------|
| **Primary author demo** | Re-run bank categorisation after fixes land; keep `8a2cef12…` as **backup** only | Completes with PASS but 70% uncategorised weakens finance UX story |
| **Backup author demo** | Current `8a2cef12…` archive acceptable if presenter acknowledges uncategorised warning | Shows honest validation + warning surfacing |
| **Repair demo** | Re-run repair after fixes; current `a4d9cb75…` usable as backup with caveats | Repair succeeds with real pytest evidence, but provenance/risk/report wording issues undermine audit narrative |
| **Presentation-ready** | Neither archive is presentation-ready without code fixes + fresh runs | Provenance, budget, path, and report clarity issues are visible to technical operators |

**Future sessions:** General code-path fixes (not archive edits) should prevent recurrence for the same failure classes.

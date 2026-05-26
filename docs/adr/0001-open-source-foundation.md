# ADR-0001 — Open-source foundation

**Status:** accepted
**Date:** 2026-05-21

## Context

The Zalos take-home requires starting from an open-source AI coding-assistant template, framework, or starter project, and explaining what was reused vs. changed. We have ~3.5 days of solo build time and need to land a working two-workflow product.

## Decision

Use the **OpenHands-inspired minimal implementation** strategy: study the OpenHands CodeAct agent design and reimplement its load-bearing patterns (action/observation model, event-stream-driven state, bounded loop with explicit termination) in a small original Python file (`apps/api/src/agentforge/agent/loop.py`, ~280 lines). No OpenHands code is imported, vendored, or copied.

Hold Aider-as-library as a documented fallback contingent on a 2-hour spike result (Thursday evening). The spike result was that the OpenHands-inspired path was tractable; Aider was not invoked.

## Options considered

| Option | Reuse | Build cost | Honesty | Defensibility | Verdict |
|---|---|---|---|---|---|
| A. Full OpenHands fork | Runtime, event stream, UI scaffold | 0.5–1.5 days just understanding | High | High if author can speak to specific modules | Too heavy for 3.5-day solo |
| **B. OpenHands-inspired minimal** | Patterns only | ~280 LOC | High with careful framing | High | **Selected (primary)** |
| C. Aider-as-library | `aider.coders.Coder` for edit/patch + git auto-commit | Medium | High (genuine library use) | High | Selected as fallback; not invoked |
| D. SWE-agent-inspired | Action format + trajectory model | Medium | Medium (niche reference) | Medium | Cut |
| E. Custom with no claimed foundation | Nothing | Low | Low (honesty problem) | Low | Cut |

## Rationale

- The OpenHands-inspired-minimal path satisfies the assignment wording ("start from an open-source AI coding-assistant template, framework, or starter project") because OpenHands is the canonical autonomous coding-assistant framework and we genuinely studied and adapted its design.
- It keeps us in code we wrote, which is defensible end-to-end and avoids the multi-day learning curve of an unfamiliar large codebase.
- The honesty story is clean: README states verbatim what was adapted (patterns) vs. replaced (everything else), with pinned commit and named files studied.
- Aider-as-library was a real fallback with a known unlock path (a 2-hour spike to verify `aider.coders.Coder` could be driven programmatically); we did not need to invoke it.

## Consequences

- The agent loop is ~280 LOC of original code, easy to audit and defend in a architecture review.
- The README's foundation section is precise and verifiable: "no OpenHands code was imported, vendored, or copied."
- We forgo OpenHands' production-grade sandbox runtime and UI; we replace them with our subprocess sandbox (ADR-0003) and Next.js wizard (ADR-0002).
- The pattern adoption is documented in `ARCHITECTURE.md` §4 (foundation attribution, verbatim) and cross-referenced in this ADR.

## Reversal condition

Reverse to Option A (full OpenHands fork) only if: (a) we discover during the build that OpenHands integrates faster than expected (not the case here), or (b) a downstream production deployment requires a feature-rich runtime we don't want to maintain ourselves.

Reverse to Option C (Aider-as-library) if the agent loop encounters edit-format pathologies that a battle-tested library would handle better. Trigger: ≥2 hours lost to edit-block edge cases.

## Pinned reference

- **Upstream:** [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands), MIT-licensed.
- **Commit studied:** `3515cb085834623d9d0ef9d6977d1bcfa2b6eb57` (fetched from `main` on 2026-05-22).
- **Files studied:** `openhands/controller/agent_controller.py`, `openhands/events/`.
- **Refresh protocol:** if submitting after the fetched date, fetch the current `main` SHA from upstream and update all four byte-identical references — README.md, ARCHITECTURE.md §4, this ADR, and LICENSE. The surrounding wording is verbatim across all four.

## Foundation attribution (canonical wording — byte-identical to README + ARCHITECTURE + LICENSE)

> AgentForge's agent loop is implemented in `apps/api/src/agentforge/agent/loop.py` (~280 lines). The action/observation model, event-stream-driven state, and bounded loop with explicit termination are adapted from OpenHands' CodeAct agent design (https://github.com/All-Hands-AI/OpenHands, commit `3515cb085834623d9d0ef9d6977d1bcfa2b6eb57`, principally the files `openhands/controller/agent_controller.py` and `openhands/events/`). No OpenHands code was imported, vendored, or copied; the patterns were studied and reimplemented in a minimal form tailored to AgentForge's two finance workflows. Everything else — the wizard UI, workspace, sandbox wrapper, finance-domain tool registry, workflow orchestrator, validation system, fixtures, evals, and repair report — is original. A fallback option (Aider-as-library) was considered and held in reserve; it was not used.

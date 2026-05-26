# ADR-0010 — Scope exclusions

**Status:** accepted
**Date:** 2026-05-21

## Context

The take-home has a 3.5-day solo build budget. Maximal does not mean "include everything"; it means optimising for assignment coverage + working demo + technical credibility + finance-user usefulness. This ADR records what is deliberately out of the prototype and what is documented as a production extension.

## Decision

### Documented as production extension (`DEPLOYMENT.md`)

| Item | Why deferred |
|---|---|
| Docker per-session sandbox | Subprocess + cwd pin + timeout is sufficient for synthetic single-user demo; Docker is a runner-interface swap |
| Hosted sandbox provider (E2B / Modal) | Vendor dependency; runner-interface swap |
| Integrity-chained audit log (storage-level CHECK + REVOKE UPDATE) | `events.jsonl` by convention is sufficient for the prototype demo; storage-level immutability is compliance-grade |
| Postgres + Redis | SQLite is sufficient for single-user durable state |
| SSO / OIDC authentication and multi-tenant isolation | Single demo-user identity for prototype |
| OpenTelemetry observability + hosted dashboards | structlog with `session_id` correlation is sufficient |
| Cloud Run / ECS deployment | Local-run via `make demo` satisfies "setup and run instructions" |
| Template marketplace and versioned template registry | One polished template (`bank_categoriser`) demonstrates the surface |
| Broader eval suite (per-tag rates, paraphrase robustness, adversarial red team) | Three scenarios (1 author + 1 repair + 1 adversarial) demonstrate the surface |
| RAG over an organisation's policy library | No retrieval need in either workflow |
| Vendor-specific extraction fine-tuning | Not justified at prototype scope |
| Multi-agent author for complex decomposable workflows | Single-agent suffices for the chosen workflow |
| Per-tenant cost budgets and dashboards | Single-tenant prototype |
| Real PII detector on uploads | Synthetic-only banner + reminder in README suffices for the demo |

### Out of scope (not implemented, not in production roadmap for this submission)

| Item | Why |
|---|---|
| Real banking / accounting / payment APIs | Assignment forbids — synthetic data only |
| Real customer data | Assignment forbids |
| Full auth | Single demo cookie identity |
| Multi-tenant security | Single-org prototype |
| Deployment | Local-run satisfies the assignment |
| PDF OCR | Assignment scope is CSV/XLSX |
| Streaming model tokens | Distracts finance users; not required |
| SSE live updates | Polling is sufficient |
| Six author templates | One polished template demonstrates the surface |
| Multiple repair fixtures | One polished fixture demonstrates the surface |
| Adversarial evals beyond one | One CSV-content injection case demonstrates the surface |
| Full OpenHands UI reuse | Wizard UI is finance-specific by design (see ADR-0002) |

## Options considered

The alternative was including more items in the prototype to look more complete. Two specific contemplated additions and why they were declined:

| Addition | Why not |
|---|---|
| Docker per-session sandbox | Would consume 3–6h for marginal demo-visible benefit; documented as next step |
| Three author templates | Would consume 4–6h for one template's marginal demo value; one template demonstrates the surface |

## Rationale

The build objective is a working, coherent, finance-user-relevant prototype within the solo time budget. Adding components beyond the must-have set increases delivery risk (integration debt, half-built features) faster than it increases value. Every excluded item is one or both of: (a) explicitly out of scope per the assignment, (b) feasible to defer with a credible production-extension story documented in `DEPLOYMENT.md`.

## Consequences

- `DEPLOYMENT.md` becomes a load-bearing artifact: it documents what we would build next in production, with concrete commands and patterns.
- The README's "Known limitations" section names every excluded production feature honestly.
- The decision to exclude is reversible: each entry in the table above could move into the prototype in a future build with documented effort.

## Reversal condition

Items in the "documented as production extension" table can be reversed item-by-item when there is product or compliance demand (post-take-home productionisation).

Items in the "out of scope" table — real APIs, real customer data, full auth, multi-tenancy — require a structural product expansion and would be addressed by separate ADRs.

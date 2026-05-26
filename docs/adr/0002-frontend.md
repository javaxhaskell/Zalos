# ADR-0002 — Frontend choice

**Status:** accepted
**Date:** 2026-05-21

## Context

The product is finance-user-facing. The assignment requires "a UI or faithful prototype for both workflows," and the user-friendly framing matters. Finance users want forms, panels, diffs, and structured Q&A — not a chat box.

## Decision

Use **Next.js 14 App Router + Tailwind + shadcn/ui** for five screens:

1. `/` — dashboard
2. `/author/[sid]` — author wizard
3. `/repair/[sid]` — repair wizard
4. `/sessions/[sid]/audit` — audit + downloads
5. `/admin/evals` — latest eval results

Read-heavy pages use React Server Components. Interactive panels (file dropzone, approval, Q&A) are client components. TanStack Query for client cache. Polling at 2-second intervals while a session is `RUNNING`; stop on `PAUSED_*` or terminal.

## Options considered

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Next.js 14 + Tailwind + shadcn/ui** | Polished, finance-presentable, RSC, typed routes | Higher up-front setup than Streamlit | **Selected** |
| Streamlit | Very fast to build; Python-native | Looks like a research tool; weak for finance demo | Held as fallback if scaffold stalls |
| HTMX + Tailwind + FastAPI | Fast iteration, minimal JS | Less polish per hour for the wizard UX | Not selected |
| Pure React + Vite + FastAPI | Lighter than Next.js | No SSR benefit; same build cost as Next.js | Not selected |

## Rationale

- The "user-friendly for finance users without terminals" requirement is the headline product attribute. UI polish is load-bearing for the pitch.
- Next.js is in the project stack; setup cost is bounded.
- shadcn/ui gives a presentable design without authoring a design system.
- Five screens is the minimum complete surface: dashboard + two wizards + audit + eval results.
- Polling at 2s is sufficient for a single-user demo; SSE is documented as a production extension but not required.

## Consequences

- Frontend build is 4–6 hours scoped to 5 screens with shadcn/ui primitives.
- TypeScript strict + types imported only from `packages/shared-schemas` (no `any`).
- No state management library beyond `useState` + TanStack Query.
- No emojis in UI text.
- Playwright happy-author and happy-repair smoke tests gate the frontend.

## Reversal condition

Switch to Streamlit if the Friday-evening checkpoint shows fewer than two screens functional. Streamlit delivers a working app over a polished one — acceptable degradation if the build hits time pressure.

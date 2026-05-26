"""Bounded agent loop — the executor for the LLM's typed plans.

Adapted from OpenHands' CodeAct agent design (action/observation model,
event-stream-driven state, bounded loop with explicit termination —
see :file:`ARCHITECTURE.md` §4 for the verbatim attribution). No
OpenHands code is imported or copied; the patterns were studied and
reimplemented in a minimal form tailored to AgentForge's two finance
workflows.

The loop is the only place that dispatches tools (INV-2). Each step:

1. Check budgets (steps, tokens, wall). On exhaustion: emit
   ``BUDGET_EXHAUSTED`` and return.
2. Call :class:`ModelClient` with the phase-filtered tool set. Emit
   ``MODEL_CALLED``.
3. If the model returned no tool calls, terminate cleanly.
4. For each ``tool_use``: resolve in the registry, validate args
   (Pydantic strict), check the idempotency cache, check the approval
   gate (INV-3), authorize, dispatch through the handler. Emit
   ``TOOL_INVOKED`` + ``TOOL_OBSERVED``.
5. Pause if any tool requires approval — caller resumes after the
   approval endpoint records ``APPROVAL_GRANTED``.
6. Otherwise append the observations to the message history and loop.

Validation failures (INV-8) get one re-prompt: the failure is surfaced
as an observation; the model sees the structured error and can retry
with corrected args. After three consecutive validation failures across
the run, the loop terminates with ``VALIDATION_LOOP_EXHAUSTED`` (INV-12
upper bound on re-prompting).
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError

from agentforge.agent import observation as obs_factory
from agentforge.agent.observation import Observation, ObservationKind
from agentforge.config import Settings
from agentforge.models import (
    ModelClient,
    ModelClientError,
    ModelMessage,
    ModelResponse,
    ToolResultBlock,
    ToolUseBlock,
)
from agentforge.persistence.event_log import EventLog
from agentforge.persistence.idempotency_store import (
    IdempotencyConflictError,
    IdempotencyStore,
    request_hash_for,
)
from agentforge.persistence.workspace import WorkspaceManager
from agentforge.schemas import (
    ActorType,
    ApprovalStatus,
    ErrorCode,
    EventKind,
    ToolPhase,
)
from agentforge.tools import (
    RegisteredTool,
    ToolContext,
    ToolNotFoundError,
    ToolRegistry,
    canonical_args_json,
    derive_idempotency_key,
)

_logger = logging.getLogger("agentforge.agent.loop")

_MAX_VALIDATION_FAILURES = 3
"""Loop-wide upper bound on Pydantic re-prompts before terminating. Set
higher than 1 so a model that fixes its first mistake but stumbles on
a later unrelated call doesn't get killed; lower than the step cap so
re-prompt storms terminate before the wall budget."""

_FINALISE_TOOL_NAME = "finalise_session"
_ASK_USER_TOOL_NAME = "ask_user"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LoopBudgets:
    """Per-run budget envelope. Hard caps; warns at 75% in BP5c."""

    max_steps: int
    max_tokens: int
    max_wall_seconds: int


@dataclass
class LoopOutcome:
    """What the loop did when control returned to the caller."""

    terminated: bool
    paused: bool
    pause_reason: str | None
    steps_taken: int
    tokens_used: int
    wall_seconds: float
    messages: list[ModelMessage]
    """Final message history. The caller persists this if a paused loop
    will be resumed; on terminate it is informational."""
    last_response: ModelResponse | None
    last_observations: list[Observation] = field(default_factory=list)
    terminal_error_code: ErrorCode | None = None


# ---------------------------------------------------------------------------
# AgentLoop
# ---------------------------------------------------------------------------


class AgentLoop:
    """Drives one workflow phase. Construct once; call :meth:`run` per phase."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        model_client: ModelClient,
        idempotency_store: IdempotencyStore,
        event_log: EventLog,
        workspace_manager: WorkspaceManager,
        settings: Settings,
    ) -> None:
        self.registry = registry
        self.model_client = model_client
        self.idempotency_store = idempotency_store
        self.event_log = event_log
        self.workspace_manager = workspace_manager
        self.settings = settings

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(
        self,
        *,
        session_id: UUID,
        phase: ToolPhase,
        system_prompt: str,
        initial_messages: list[ModelMessage],
        budgets: LoopBudgets,
        start_step: int = 0,
        advisory_mode: bool = False,
    ) -> LoopOutcome:
        """Drive one phase to a terminal outcome.

        When ``advisory_mode=True``, terminal LLM problems (validation
        loop exhausted, budget cap, model client error) are recorded
        as ``DECISION_INPUT`` events with
        ``kind="llm_advisory_incomplete"`` instead of ``WORKFLOW_FAILED``
        / ``BUDGET_EXHAUSTED``. This is the right shape when the LLM
        is advisory only. The loop still returns a ``LoopOutcome`` with the
        same ``terminal_error_code`` so the caller can branch on it;
        advisory_mode only controls event wording.
        """
        wall_start = time.monotonic()
        messages: list[ModelMessage] = list(initial_messages)
        step = start_step
        tokens_used = 0
        validation_failures = 0
        last_response: ModelResponse | None = None
        last_observations: list[Observation] = []

        while True:
            # 1. Budget check (INV-12)
            wall_elapsed = time.monotonic() - wall_start
            steps_taken = step - start_step
            budget_exhaustion = self._check_budgets(
                steps_taken=steps_taken,
                tokens_used=tokens_used,
                wall_elapsed=wall_elapsed,
                budgets=budgets,
            )
            if budget_exhaustion is not None:
                self._emit_budget_exhausted(
                    session_id=session_id,
                    step=step,
                    error_code=budget_exhaustion,
                    advisory_mode=advisory_mode,
                )
                return LoopOutcome(
                    terminated=True,
                    paused=False,
                    pause_reason=None,
                    steps_taken=steps_taken,
                    tokens_used=tokens_used,
                    wall_seconds=wall_elapsed,
                    messages=messages,
                    last_response=last_response,
                    terminal_error_code=budget_exhaustion,
                )

            # 2. Model call
            tools_for_phase = self.registry.list_for_phase(phase)
            model_call_t0 = time.monotonic()
            try:
                response = await self.model_client.complete(
                    system_prompt=system_prompt,
                    messages=messages,
                    tools=tools_for_phase,
                    max_tokens=4096,
                )
            except ModelClientError as exc:
                _logger.error("model client error", exc_info=exc)
                if advisory_mode:
                    # Advisory LLM call failed — informational, not a
                    # workflow failure.
                    self.event_log.append(
                        session_id=session_id,
                        kind=EventKind.DECISION_INPUT,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "kind": "llm_advisory_incomplete",
                            "reason": "model_client_error",
                            "message": str(exc)[:400],
                        },
                        step=step,
                    )
                else:
                    self.event_log.append(
                        session_id=session_id,
                        kind=EventKind.WORKFLOW_FAILED,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "error_code": ErrorCode.UNKNOWN.value,
                            "message": f"model client error: {exc}"[:400],
                            "stage": "agent_loop",
                        },
                        step=step,
                    )
                return LoopOutcome(
                    terminated=True,
                    paused=False,
                    pause_reason=None,
                    steps_taken=steps_taken,
                    tokens_used=tokens_used,
                    wall_seconds=time.monotonic() - wall_start,
                    messages=messages,
                    last_response=last_response,
                    terminal_error_code=ErrorCode.UNKNOWN,
                )

            tokens_used += response.usage.total_tokens
            last_response = response
            model_call_latency_ms = int((time.monotonic() - model_call_t0) * 1000)
            self._emit_model_called(
                session_id=session_id,
                step=step,
                system_prompt=system_prompt,
                messages=messages,
                response=response,
                latency_ms=model_call_latency_ms,
            )
            messages.append(ModelMessage(role="assistant", content=response.content))

            tool_uses = response.tool_uses
            # 3. Termination — model produced text only, no actions.
            if not tool_uses:
                return LoopOutcome(
                    terminated=True,
                    paused=False,
                    pause_reason=None,
                    steps_taken=steps_taken + 1,
                    tokens_used=tokens_used,
                    wall_seconds=time.monotonic() - wall_start,
                    messages=messages,
                    last_response=response,
                    last_observations=[],
                )

            # 4. Dispatch each tool_use; collect observations for the next turn.
            observations: list[Observation] = []
            tool_results: list[ToolResultBlock] = []
            paused = False
            finalise_observed = False

            for use in tool_uses:
                observation = await self._dispatch_one(
                    session_id=session_id,
                    step=step,
                    use=use,
                    phase=phase,
                )
                observations.append(observation)
                if observation.kind == ObservationKind.VALIDATION_FAILED:
                    validation_failures += 1
                tool_results.append(
                    ToolResultBlock(
                        tool_use_id=use.id,
                        content=json.dumps(observation.payload, separators=(",", ":")),
                        is_error=observation.error_code is not None,
                    )
                )
                if observation.kind == ObservationKind.APPROVAL_REQUIRED:
                    paused = True
                    pause_reason = "approval"
                    last_observations = observations
                    break
                if (
                    use.name == _ASK_USER_TOOL_NAME
                    and observation.kind == ObservationKind.SUCCESS
                ):
                    paused = True
                    pause_reason = "user"
                    last_observations = observations
                    break
                if (
                    use.name == _FINALISE_TOOL_NAME
                    and observation.kind == ObservationKind.SUCCESS
                ):
                    finalise_observed = True

            messages.append(ModelMessage(role="user", content=list(tool_results)))

            if paused:
                return LoopOutcome(
                    terminated=False,
                    paused=True,
                    pause_reason=pause_reason,
                    steps_taken=steps_taken + 1,
                    tokens_used=tokens_used,
                    wall_seconds=time.monotonic() - wall_start,
                    messages=messages,
                    last_response=response,
                    last_observations=last_observations,
                )

            if validation_failures >= _MAX_VALIDATION_FAILURES:
                if advisory_mode:
                    # Advisory LLM run — record as informational, NOT a
                    # workflow failure.
                    self.event_log.append(
                        session_id=session_id,
                        kind=EventKind.DECISION_INPUT,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "kind": "llm_advisory_incomplete",
                            "reason": ErrorCode.VALIDATION_LOOP_EXHAUSTED.value,
                            "message": (
                                f"advisory LLM run hit {_MAX_VALIDATION_FAILURES} "
                                "consecutive tool-validation failures."
                            ),
                        },
                        step=step,
                    )
                else:
                    self.event_log.append(
                        session_id=session_id,
                        kind=EventKind.WORKFLOW_FAILED,
                        actor_type=ActorType.SYSTEM,
                        payload={
                            "error_code": ErrorCode.VALIDATION_LOOP_EXHAUSTED.value,
                            "message": (
                                f"agent loop exceeded {_MAX_VALIDATION_FAILURES} "
                                "consecutive validation failures"
                            ),
                            "stage": "agent_loop",
                        },
                        step=step,
                    )
                return LoopOutcome(
                    terminated=True,
                    paused=False,
                    pause_reason=None,
                    steps_taken=steps_taken + 1,
                    tokens_used=tokens_used,
                    wall_seconds=time.monotonic() - wall_start,
                    messages=messages,
                    last_response=response,
                    last_observations=observations,
                    terminal_error_code=ErrorCode.VALIDATION_LOOP_EXHAUSTED,
                )

            if finalise_observed:
                return LoopOutcome(
                    terminated=True,
                    paused=False,
                    pause_reason=None,
                    steps_taken=steps_taken + 1,
                    tokens_used=tokens_used,
                    wall_seconds=time.monotonic() - wall_start,
                    messages=messages,
                    last_response=response,
                    last_observations=observations,
                )

            step += 1

    # ------------------------------------------------------------------
    # Per-tool dispatch
    # ------------------------------------------------------------------

    async def _dispatch_one(
        self,
        *,
        session_id: UUID,
        step: int,
        use: ToolUseBlock,
        phase: ToolPhase,
    ) -> Observation:
        # Registry lookup
        try:
            tool = self.registry.get(use.name)
        except ToolNotFoundError:
            self._emit_tool_observed(
                session_id=session_id,
                step=step,
                tool_name=use.name,
                invocation_id=None,
                success=False,
                error_code=ErrorCode.UNKNOWN,
                error_message="tool not registered for this registry",
            )
            return obs_factory.not_registered(
                tool_name=use.name, available=self.registry.all_names()
            )

        if phase not in tool.definition.phases:
            self._emit_tool_observed(
                session_id=session_id,
                step=step,
                tool_name=use.name,
                invocation_id=None,
                success=False,
                error_code=ErrorCode.UNKNOWN,
                error_message="tool not registered for this phase",
            )
            return obs_factory.not_registered(
                tool_name=use.name,
                available=[
                    name
                    for name in self.registry.all_names()
                    if phase in self.registry.get(name).definition.phases
                ],
            )

        # Pydantic validation
        try:
            args = tool.input_schema.model_validate(use.input)
        except ValidationError as exc:
            self._emit_tool_observed(
                session_id=session_id,
                step=step,
                tool_name=use.name,
                invocation_id=None,
                success=False,
                error_code=ErrorCode.UNKNOWN,
                error_message="input validation failed",
            )
            return obs_factory.validation_failed(
                tool_name=use.name,
                message="input validation failed",
                errors=cast(list[dict[str, Any]], exc.errors()),
            )

        # Idempotency cache lookup
        canonical = canonical_args_json(args)
        idem_key = derive_idempotency_key(
            session_id=session_id,
            tool_name=use.name,
            step=step,
            canonical_args=canonical,
        )
        req_hash = request_hash_for(canonical)
        cached = self.idempotency_store.get(idem_key)
        if cached is not None:
            if cached.request_hash != req_hash:
                # Same key + different args → conflict (INV-7).
                self._emit_tool_observed(
                    session_id=session_id,
                    step=step,
                    tool_name=use.name,
                    invocation_id=None,
                    success=False,
                    error_code=ErrorCode.UNKNOWN,
                    error_message="idempotency conflict",
                )
                return obs_factory.handler_error(
                    tool_name=use.name,
                    error_code=ErrorCode.UNKNOWN,
                    message="idempotency conflict: same key, different args",
                    latency_ms=0,
                )
            self._emit_tool_observed(
                session_id=session_id,
                step=step,
                tool_name=use.name,
                invocation_id=None,
                success=True,
                error_code=None,
                error_message=None,
                payload={"cache_hit": True, **cached.payload},
            )
            return obs_factory.cache_hit(
                tool_name=use.name, payload=cached.payload
            )

        # Approval gate (INV-3) — write tools must have APPROVAL_GRANTED.
        if tool.definition.requires_approval and not self._has_approval(
            session_id=session_id, step=step, tool_name=use.name
        ):
            request_id = self._record_approval_request(
                session_id=session_id, step=step, tool_name=use.name
            )
            return obs_factory.approval_required(
                tool_name=use.name, request_id=request_id
            )

        # Authorize
        try:
            authorize = self._resolve_authorize(tool.definition.authorize_callable)
        except (ImportError, AttributeError) as exc:
            return obs_factory.handler_error(
                tool_name=use.name,
                error_code=ErrorCode.UNKNOWN,
                message=f"authorize_callable unresolvable: {exc}",
                latency_ms=0,
            )

        ctx = ToolContext(
            session_id=session_id,
            step=step,
            workspace_manager=self.workspace_manager,
            event_log=self.event_log,
            settings=self.settings,
        )
        try:
            authorize(ctx)
        except Exception as exc:  # noqa: BLE001 — domain-specific ForbiddenError
            return obs_factory.forbidden(tool_name=use.name, message=str(exc))

        # Dispatch
        self._emit_tool_invoked(
            session_id=session_id,
            step=step,
            tool_name=use.name,
            idempotency_key=idem_key,
            args_hash=req_hash,
        )
        t0 = time.monotonic()
        try:
            result: BaseModel = await tool.handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 — surface any handler failure
            latency_ms = int((time.monotonic() - t0) * 1000)
            error_code = _classify_handler_exception(exc)
            _logger.exception("tool handler raised", extra={"tool": use.name})
            self._emit_tool_observed(
                session_id=session_id,
                step=step,
                tool_name=use.name,
                invocation_id=None,
                success=False,
                error_code=error_code,
                error_message=str(exc),
            )
            return obs_factory.handler_error(
                tool_name=use.name,
                error_code=error_code,
                message=str(exc),
                latency_ms=latency_ms,
            )
        latency_ms = int((time.monotonic() - t0) * 1000)

        payload = result.model_dump(mode="json")

        # Cache the observation BEFORE returning so a retry within this
        # step is deduped. Conflict is treated as a handler error.
        try:
            self.idempotency_store.put(
                key=idem_key,
                tool_name=use.name,
                request_hash=req_hash,
                response_payload=payload,
            )
        except IdempotencyConflictError as exc:
            return obs_factory.handler_error(
                tool_name=use.name,
                error_code=ErrorCode.UNKNOWN,
                message=str(exc),
                latency_ms=latency_ms,
            )

        self._emit_tool_observed(
            session_id=session_id,
            step=step,
            tool_name=use.name,
            invocation_id=None,
            success=True,
            error_code=None,
            error_message=None,
            payload=payload,
        )
        return obs_factory.success(
            tool_name=use.name, payload=payload, latency_ms=latency_ms
        )

    # ------------------------------------------------------------------
    # Budgets
    # ------------------------------------------------------------------

    def _check_budgets(
        self,
        *,
        steps_taken: int,
        tokens_used: int,
        wall_elapsed: float,
        budgets: LoopBudgets,
    ) -> ErrorCode | None:
        if steps_taken >= budgets.max_steps:
            return ErrorCode.BUDGET_EXHAUSTED_STEPS
        if tokens_used >= budgets.max_tokens:
            return ErrorCode.BUDGET_EXHAUSTED_TOKENS
        if wall_elapsed >= budgets.max_wall_seconds:
            return ErrorCode.BUDGET_EXHAUSTED_WALL_TIME
        return None

    def _emit_budget_exhausted(
        self,
        *,
        session_id: UUID,
        step: int,
        error_code: ErrorCode,
        advisory_mode: bool = False,
    ) -> None:
        if advisory_mode:
            # In advisory mode the orchestrator owns the budget for
            # the wider session; the LLM hitting its slice's cap is
            # an informational signal, not a workflow blocker.
            self.event_log.append(
                session_id=session_id,
                kind=EventKind.DECISION_INPUT,
                actor_type=ActorType.SYSTEM,
                payload={
                    "kind": "llm_advisory_incomplete",
                    "reason": error_code.value,
                    "message": (
                        "advisory LLM run hit its per-call budget."
                    ),
                },
                step=step,
            )
            return
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.BUDGET_EXHAUSTED,
            actor_type=ActorType.SYSTEM,
            payload={"error_code": error_code.value},
            step=step,
        )

    # ------------------------------------------------------------------
    # Approval gate
    # ------------------------------------------------------------------

    def _has_approval(
        self, *, session_id: UUID, step: int, tool_name: str
    ) -> bool:
        """Return True if this (step, tool_name) is approved.

        Two forms of approval are honoured:

          * **Per-step grant** — an ``APPROVAL_GRANTED`` event tagged
            with this ``step`` (either as the event's own ``step``
            attribute or in ``payload.step``). This is what the
            ``/approve`` HTTP endpoint emits.
          * **Covering grant** — an ``APPROVAL_GRANTED`` with
            ``payload.scope == "session"`` and matching
            ``payload.tool_name``. Per ADR-0006, the user's first
            run-consent for ``run_python_script`` / ``run_pytest``
            covers all subsequent same-tool calls in the session.
            The orchestrator emits this covering grant once.
        """
        events = self.event_log.read_all(session_id)
        for evt in reversed(events):
            if evt.kind != EventKind.APPROVAL_GRANTED:
                continue
            # Per-step grant
            if evt.step == step or evt.payload.get("step") == step:
                return True
            # Covering grant for this tool name (ADR-0006 run-consent)
            if (
                evt.payload.get("scope") == "session"
                and evt.payload.get("tool_name") == tool_name
            ):
                return True
        return False

    def _record_approval_request(
        self, *, session_id: UUID, step: int, tool_name: str
    ) -> str:
        from uuid import uuid4

        request_id = str(uuid4())
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.APPROVAL_REQUESTED,
            actor_type=ActorType.SYSTEM,
            payload={
                "request_id": request_id,
                "tool_name": tool_name,
                "step": step,
                "status": ApprovalStatus.PENDING.value,
            },
            step=step,
        )
        return request_id

    # ------------------------------------------------------------------
    # Authorize resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_authorize(dotted_path: str) -> Any:
        module_path, _, attr = dotted_path.rpartition(".")
        if not module_path:
            raise ImportError(f"authorize_callable must be dotted: {dotted_path!r}")
        module = importlib.import_module(module_path)
        return getattr(module, attr)

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    def _emit_model_called(
        self,
        *,
        session_id: UUID,
        step: int,
        system_prompt: str,
        messages: list[ModelMessage],
        response: ModelResponse,
        latency_ms: int = 0,
    ) -> None:
        prompt_hash = hashlib.sha256(
            json.dumps(
                {
                    "system": system_prompt,
                    "messages": [m.model_dump(mode="json") for m in messages],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        response_hash = hashlib.sha256(
            json.dumps(
                {
                    "id": response.id,
                    "stop_reason": response.stop_reason,
                    "content": [b.model_dump(mode="json") for b in response.content],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        usage_payload = response.usage.model_dump(mode="json")
        usage_payload["total_tokens"] = response.usage.total_tokens
        provider_meta = getattr(self.model_client, "metadata", {}) or {}
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.MODEL_CALLED,
            actor_type=ActorType.MODEL,
            payload={
                "response_id": response.id,
                "stop_reason": response.stop_reason,
                "prompt_hash": prompt_hash,
                "response_hash": response_hash,
                "latency_ms": latency_ms,
                "usage": usage_payload,
                "provider": provider_meta.get("provider", "unknown"),
                "model": provider_meta.get("model", "unknown"),
                "base_url": provider_meta.get("base_url", ""),
            },
            step=step,
        )
        from agentforge.persistence.live_budget_sync import persist_live_session_budget

        persist_live_session_budget(session_id=session_id, event_log=self.event_log)

    def _emit_tool_invoked(
        self,
        *,
        session_id: UUID,
        step: int,
        tool_name: str,
        idempotency_key: str,
        args_hash: str,
    ) -> None:
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.TOOL_INVOKED,
            actor_type=ActorType.SYSTEM,
            payload={
                "tool_name": tool_name,
                "idempotency_key": idempotency_key,
                "args_hash": args_hash,
            },
            step=step,
        )

    def _emit_tool_observed(
        self,
        *,
        session_id: UUID,
        step: int,
        tool_name: str,
        invocation_id: str | None,
        success: bool,
        error_code: ErrorCode | None,
        error_message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.event_log.append(
            session_id=session_id,
            kind=EventKind.TOOL_OBSERVED,
            actor_type=ActorType.SYSTEM,
            payload={
                "tool_name": tool_name,
                "invocation_id": invocation_id,
                "success": success,
                "error_code": error_code.value if error_code else None,
                "error_message": error_message,
                "result_keys": sorted(payload.keys()) if payload else [],
            },
            step=step,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_handler_exception(exc: BaseException) -> ErrorCode:
    """Map a handler exception to a typed ``ErrorCode``.

    BP5b/c extend this as more domain-specific exceptions appear
    (subprocess failures, golden-diff mismatches, etc.). For BP5a we
    fall back to ``UNKNOWN`` so every error surfaces with at least a
    consistent envelope.
    """
    from agentforge.persistence.workspace import WorkspaceError

    if isinstance(exc, WorkspaceError):
        return ErrorCode.UNKNOWN  # path-traversal is a logical error, not a class of its own yet
    if isinstance(exc, FileNotFoundError):
        return ErrorCode.UNKNOWN
    return ErrorCode.UNKNOWN


__all__ = [
    "AgentLoop",
    "LoopBudgets",
    "LoopOutcome",
    "RegisteredTool",  # re-export for convenience
]

"""Shared enums + base model.

Every other schema module imports from here. Adding an enum variant requires
an ADR (see ``CONTRACTS.md`` §9 versioning policy).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Pydantic v2 base for AgentForge boundary types.

    Configured with ``extra="forbid"`` (rejects unknown fields — the
    load-bearing protection against contract drift) and ``strict=False``
    (the default — permits JSON-native string→enum coercion at the API
    boundary, which is the canonical input format).

    Internal modules that must reject string→enum coercion (e.g., when
    accepting Python dicts from another internal module) should use
    ``model_validate_json`` rather than ``model_validate``, OR use
    Annotated[…, Strict()] on the specific field.
    """

    model_config = ConfigDict(extra="forbid")


class Workflow(StrEnum):
    AUTHOR = "author"
    REPAIR = "repair"


class SessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED_USER = "paused_user"
    PAUSED_APPROVAL = "paused_approval"
    COMPLETED = "completed"
    FAILED_BUDGET = "failed_budget"
    FAILED_MODEL = "failed_model"
    FAILED_SANDBOX = "failed_sandbox"
    FAILED_USER_REJECT = "failed_user_reject"
    FAILED_OTHER = "failed_other"
    AUTO_ARCHIVED = "auto_archived"


class AuthorPhase(StrEnum):
    AUTHOR_TEMPLATE = "author_template"
    AUTHOR_UPLOAD = "author_upload"
    AUTHOR_PROFILE = "author_profile"
    AUTHOR_DESCRIBE = "author_describe"
    AUTHOR_INFER = "author_infer"
    AUTHOR_QA = "author_qa"
    AUTHOR_CONFIRM = "author_confirm"
    AUTHOR_GENERATE = "author_generate"
    AUTHOR_REVIEW_DIFF = "author_review_diff"
    AUTHOR_APPLIED = "author_applied"
    AUTHOR_RUN = "author_run"
    AUTHOR_VALIDATE = "author_validate"
    AUTHOR_REVIEW = "author_review"
    AUTHOR_FINALISE = "author_finalise"


class RepairPhase(StrEnum):
    REPAIR_UPLOAD = "repair_upload"
    REPAIR_LOADED = "repair_loaded"
    REPAIR_PROBLEM = "repair_problem"
    REPAIR_TRIAGE = "repair_triage"
    REPAIR_CONFIRM_SUMMARY = "repair_confirm_summary"
    REPAIR_REPRODUCE = "repair_reproduce"
    REPAIR_NEED_INFO = "repair_need_info"
    REPAIR_DIAGNOSE = "repair_diagnose"
    REPAIR_PROPOSE = "repair_propose"
    REPAIR_REVIEW_PATCH = "repair_review_patch"
    REPAIR_APPLY = "repair_apply"
    REPAIR_VALIDATE = "repair_validate"
    REPAIR_REPORT = "repair_report"
    REPAIR_FINALISE = "repair_finalise"


class ToolPhase(StrEnum):
    """Coarse two-phase exposure per workflow (see CONTRACTS.md §4)."""

    AUTHOR_INFO = "author.info"
    AUTHOR_BUILD = "author.build"
    REPAIR_INFO = "repair.info"
    REPAIR_FIX = "repair.fix"


class RiskLevel(StrEnum):
    READ = "read"
    LOW_WRITE = "low_write"
    HIGH_WRITE = "high_write"


class ActorType(StrEnum):
    USER = "user"
    SYSTEM = "system"
    MODEL = "model"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    GRANTED = "granted"
    DECLINED = "declined"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCode(StrEnum):
    # Upload
    MALFORMED_CSV = "malformed_csv"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FILE_TOO_LARGE = "file_too_large"
    UPLOAD_LIMIT_EXCEEDED = "upload_limit_exceeded"
    # Schema / validation
    MISSING_REQUIRED_COLUMNS = "missing_required_columns"
    AMBIGUOUS_SCHEMA = "ambiguous_schema"
    # Tool / loop
    TOOL_NOT_REGISTERED = "tool_not_registered"
    VALIDATION_LOOP_EXHAUSTED = "validation_loop_exhausted"
    GENERATED_CODE_FAILED = "generated_code_failed"
    TEST_FAILED = "test_failed"
    COMMAND_TIMEOUT = "command_timeout"
    # Repair
    REPAIR_CANNOT_REPRODUCE = "repair_cannot_reproduce"
    # Author
    AUTHOR_INTENT_SCHEMA_MISMATCH = "author_intent_schema_mismatch"
    AUTHOR_CUSTOM_WORKFLOW_NOT_VALIDATED = "author_custom_workflow_not_validated"
    AUTHOR_CUSTOM_BUILD_FAILED = "author_custom_build_failed"
    AUTHOR_VALIDATION_FAILED = "author_validation_failed"
    UNIVERSAL_VALIDATION_FAILED = "universal_validation_failed"
    CONTRACT_SPECIFIC_VALIDATION_FAILED = "contract_specific_validation_failed"
    GENERATED_PYTEST_FAILED = "generated_pytest_failed"
    GOLDEN_OUTPUT_COMPARISON_FAILED = "golden_output_comparison_failed"
    SAFETY_VALIDATION_FAILED = "safety_validation_failed"
    ARTIFACT_VALIDATION_FAILED = "artifact_validation_failed"
    AUTHOR_MODEL_REQUIRED_FOR_CUSTOM_BUILD = "author_model_required_for_custom_build"
    AUTHOR_MODEL_REQUIRED_FOR_AUTHORING = "author_model_required_for_authoring"
    AUTHOR_CONTRACT_PLANNING_FAILED = "author_contract_planning_failed"
    AUTHOR_CONTRACT_CLARIFICATION_REQUIRED = "author_contract_clarification_required"
    AUTHOR_CONTRACT_REVIEW_FAILED = "author_contract_review_failed"
    AUTHOR_CODE_GENERATION_FAILED = "author_code_generation_failed"
    AUTHOR_TEST_GENERATION_FAILED = "author_test_generation_failed"
    AUTHOR_GENERATED_CODE_FAILED = "author_generated_code_failed"
    AUTHOR_MODEL_DID_NOT_CONTRIBUTE = "author_model_did_not_contribute"
    AUTHOR_VALIDATION_NOT_CONTRACT_DRIVEN = "author_validation_not_contract_driven"
    AUTHOR_NON_LLM_BUSINESS_LOGIC_DETECTED = "author_non_llm_business_logic_detected"
    AUTHOR_NON_LLM_ARTIFACT_MAKER_DETECTED = "author_non_llm_artifact_maker_detected"
    AUTHOR_REQUIRED_ARTIFACT_OVERWRITTEN = "author_required_artifact_overwritten"
    # Budgets
    BUDGET_EXHAUSTED_TOKENS = "budget_exhausted_tokens"
    BUDGET_EXHAUSTED_TOOL_CALLS = "budget_exhausted_tool_calls"
    BUDGET_EXHAUSTED_STEPS = "budget_exhausted_steps"
    BUDGET_EXHAUSTED_WALL_TIME = "budget_exhausted_wall_time"
    BUDGET_EXHAUSTED_FILE_COUNT = "budget_exhausted_file_count"
    # Approval / user
    APPROVAL_DECLINED = "approval_declined"
    USER_ABANDONED = "user_abandoned"
    # Infrastructure
    SANDBOX_CRASH = "sandbox_crash"
    WORKFLOW_INTERRUPTED = "workflow_interrupted"
    UNKNOWN = "unknown"


class TerminalSessionStatus(StrEnum):
    """Subset of SessionStatus that are terminal (no further transitions)."""

    COMPLETED = "completed"
    FAILED_BUDGET = "failed_budget"
    FAILED_MODEL = "failed_model"
    FAILED_SANDBOX = "failed_sandbox"
    FAILED_USER_REJECT = "failed_user_reject"
    FAILED_OTHER = "failed_other"
    AUTO_ARCHIVED = "auto_archived"

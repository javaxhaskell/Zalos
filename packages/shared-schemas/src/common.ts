/**
 * Shared enums for AgentForge.
 *
 * Mirrors `apps/api/src/agentforge/schemas/common.py`. The frontend
 * imports these values from `@agentforge/shared-schemas` and never
 * invents shapes. Adding a value requires an ADR via `agentforge-architect`.
 */

export const Workflow = {
  AUTHOR: 'author',
  REPAIR: 'repair',
} as const;
export type Workflow = (typeof Workflow)[keyof typeof Workflow];

export const SessionStatus = {
  CREATED: 'created',
  RUNNING: 'running',
  PAUSED_USER: 'paused_user',
  PAUSED_APPROVAL: 'paused_approval',
  COMPLETED: 'completed',
  FAILED_BUDGET: 'failed_budget',
  FAILED_MODEL: 'failed_model',
  FAILED_SANDBOX: 'failed_sandbox',
  FAILED_USER_REJECT: 'failed_user_reject',
  FAILED_OTHER: 'failed_other',
  AUTO_ARCHIVED: 'auto_archived',
} as const;
export type SessionStatus = (typeof SessionStatus)[keyof typeof SessionStatus];

export const AuthorPhase = {
  AUTHOR_TEMPLATE: 'author_template',
  AUTHOR_UPLOAD: 'author_upload',
  AUTHOR_PROFILE: 'author_profile',
  AUTHOR_DESCRIBE: 'author_describe',
  AUTHOR_INFER: 'author_infer',
  AUTHOR_QA: 'author_qa',
  AUTHOR_CONFIRM: 'author_confirm',
  AUTHOR_GENERATE: 'author_generate',
  AUTHOR_REVIEW_DIFF: 'author_review_diff',
  AUTHOR_APPLIED: 'author_applied',
  AUTHOR_RUN: 'author_run',
  AUTHOR_VALIDATE: 'author_validate',
  AUTHOR_REVIEW: 'author_review',
  AUTHOR_FINALISE: 'author_finalise',
} as const;
export type AuthorPhase = (typeof AuthorPhase)[keyof typeof AuthorPhase];

export const RepairPhase = {
  REPAIR_UPLOAD: 'repair_upload',
  REPAIR_LOADED: 'repair_loaded',
  REPAIR_PROBLEM: 'repair_problem',
  REPAIR_TRIAGE: 'repair_triage',
  REPAIR_CONFIRM_SUMMARY: 'repair_confirm_summary',
  REPAIR_REPRODUCE: 'repair_reproduce',
  REPAIR_NEED_INFO: 'repair_need_info',
  REPAIR_DIAGNOSE: 'repair_diagnose',
  REPAIR_PROPOSE: 'repair_propose',
  REPAIR_REVIEW_PATCH: 'repair_review_patch',
  REPAIR_APPLY: 'repair_apply',
  REPAIR_VALIDATE: 'repair_validate',
  REPAIR_REPORT: 'repair_report',
  REPAIR_FINALISE: 'repair_finalise',
} as const;
export type RepairPhase = (typeof RepairPhase)[keyof typeof RepairPhase];

export const ToolPhase = {
  AUTHOR_INFO: 'author.info',
  AUTHOR_BUILD: 'author.build',
  REPAIR_INFO: 'repair.info',
  REPAIR_FIX: 'repair.fix',
} as const;
export type ToolPhase = (typeof ToolPhase)[keyof typeof ToolPhase];

export const RiskLevel = {
  READ: 'read',
  LOW_WRITE: 'low_write',
  HIGH_WRITE: 'high_write',
} as const;
export type RiskLevel = (typeof RiskLevel)[keyof typeof RiskLevel];

export const ActorType = {
  USER: 'user',
  SYSTEM: 'system',
  MODEL: 'model',
} as const;
export type ActorType = (typeof ActorType)[keyof typeof ActorType];

export const ApprovalStatus = {
  PENDING: 'pending',
  GRANTED: 'granted',
  DECLINED: 'declined',
} as const;
export type ApprovalStatus = (typeof ApprovalStatus)[keyof typeof ApprovalStatus];

export const Severity = {
  INFO: 'info',
  LOW: 'low',
  MEDIUM: 'medium',
  HIGH: 'high',
  CRITICAL: 'critical',
} as const;
export type Severity = (typeof Severity)[keyof typeof Severity];

export const ErrorCode = {
  MALFORMED_CSV: 'malformed_csv',
  UNSUPPORTED_FILE_TYPE: 'unsupported_file_type',
  FILE_TOO_LARGE: 'file_too_large',
  UPLOAD_LIMIT_EXCEEDED: 'upload_limit_exceeded',
  MISSING_REQUIRED_COLUMNS: 'missing_required_columns',
  AMBIGUOUS_SCHEMA: 'ambiguous_schema',
  TOOL_NOT_REGISTERED: 'tool_not_registered',
  VALIDATION_LOOP_EXHAUSTED: 'validation_loop_exhausted',
  GENERATED_CODE_FAILED: 'generated_code_failed',
  TEST_FAILED: 'test_failed',
  COMMAND_TIMEOUT: 'command_timeout',
  REPAIR_CANNOT_REPRODUCE: 'repair_cannot_reproduce',
  AUTHOR_CONTRACT_PLANNING_FAILED: 'author_contract_planning_failed',
  AUTHOR_CONTRACT_REVIEW_FAILED: 'author_contract_review_failed',
  AUTHOR_CONTRACT_CLARIFICATION_REQUIRED: 'author_contract_clarification_required',
  BUDGET_EXHAUSTED_TOKENS: 'budget_exhausted_tokens',
  BUDGET_EXHAUSTED_TOOL_CALLS: 'budget_exhausted_tool_calls',
  BUDGET_EXHAUSTED_STEPS: 'budget_exhausted_steps',
  BUDGET_EXHAUSTED_WALL_TIME: 'budget_exhausted_wall_time',
  BUDGET_EXHAUSTED_FILE_COUNT: 'budget_exhausted_file_count',
  APPROVAL_DECLINED: 'approval_declined',
  USER_ABANDONED: 'user_abandoned',
  SANDBOX_CRASH: 'sandbox_crash',
  UNKNOWN: 'unknown',
} as const;
export type ErrorCode = (typeof ErrorCode)[keyof typeof ErrorCode];

/** Discriminated error envelope returned by the API on any APIError subclass. */
export interface ErrorEnvelope {
  readonly error_code: ErrorCode | 'validation_error' | 'not_implemented';
  readonly message: string;
  readonly technical_detail?: string | null;
  readonly session_id?: string | null;
  readonly details?: Record<string, unknown>;
}

import type { ApprovalStatus, ErrorCode, RiskLevel, ToolPhase } from './common.js';

export interface ToolDefinition {
  readonly name: string;
  readonly description: string;
  readonly input_schema_name: string;
  readonly output_schema_name: string;
  readonly risk_level: RiskLevel;
  readonly requires_approval: boolean;
  readonly idempotent: boolean;
  readonly phases: readonly ToolPhase[];
  readonly authorize_callable: string;
  readonly adr_override: string | null;
}

export interface ToolInvocation {
  readonly id: string;
  readonly session_id: string;
  readonly step: number;
  readonly tool_name: string;
  readonly args_hash: string;
  readonly idempotency_key: string;
  readonly attempt: number;
  readonly started_at: string;
  readonly ended_at: string | null;
  readonly success: boolean | null;
}

export interface ToolObservation {
  readonly invocation_id: string;
  readonly success: boolean;
  readonly output_summary: string;
  readonly output_path: string | null;
  readonly error_code: ErrorCode | null;
  readonly error_message: string | null;
  readonly latency_ms: number;
}

export interface ApprovalRequest {
  readonly id: string;
  readonly session_id: string;
  readonly step: number;
  readonly kind: string;
  readonly business_summary: string;
  readonly diff_paths: readonly string[];
  readonly created_at: string;
  readonly status: ApprovalStatus;
}

export interface ApprovalDecision {
  readonly id: string;
  readonly request_id: string;
  readonly status: ApprovalStatus;
  readonly reason: string | null;
  readonly decided_at: string;
  readonly decided_by: string;
}

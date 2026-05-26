import type { ErrorCode, SessionStatus, Workflow } from './common.js';

export interface BudgetStatus {
  readonly tokens_used: number;
  readonly tokens_limit: number;
  readonly tool_calls_used: number;
  readonly tool_calls_limit: number;
  readonly steps_used: number;
  readonly steps_limit: number;
  readonly wall_seconds_used: number;
  readonly wall_seconds_limit: number;
  readonly file_count: number;
  readonly file_count_limit: number;
}

export interface SessionCreate {
  readonly workflow: Workflow;
}

export interface Session {
  readonly id: string;
  readonly workflow: Workflow;
  readonly status: SessionStatus;
  readonly current_phase: string | null;
  readonly current_step: number;
  readonly started_at: string;
  readonly updated_at: string;
  readonly completed_at: string | null;
  readonly workspace_path: string;
  readonly manifest_schema_version: number;
  readonly budget: BudgetStatus;
  readonly last_event_id: string | null;
  readonly terminal_error_code: ErrorCode | null;
}

export type SessionListView = 'active' | 'archived' | 'deleted';

export interface SessionListItem {
  readonly id: string;
  readonly workflow: Workflow;
  readonly status: SessionStatus;
  readonly current_phase: string | null;
  readonly started_at: string;
  readonly updated_at: string;
  readonly deleted_at?: string | null;
}

export interface SessionList {
  readonly sessions: readonly SessionListItem[];
}

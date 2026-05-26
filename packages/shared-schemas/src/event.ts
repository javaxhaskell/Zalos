import type { ActorType } from './common.js';

export const EventKind = {
  WORKFLOW_STARTED: 'workflow_started',
  WORKSPACE_ALLOCATED: 'workspace_allocated',
  TEMPLATE_SEEDED: 'template_seeded',
  FILE_UPLOADED: 'file_uploaded',
  SCHEMA_DETECTED: 'schema_detected',
  DECISION_INPUT: 'decision_input',
  QUESTION_ASKED: 'question_asked',
  ANSWER_RECEIVED: 'answer_received',
  REQUIREMENTS_DRAFTED: 'requirements_drafted',
  REQUIREMENTS_CONFIRMED: 'requirements_confirmed',
  PHASE_TRANSITIONED: 'phase_transitioned',
  MODEL_CALLED: 'model_called',
  TOOL_INVOKED: 'tool_invoked',
  TOOL_OBSERVED: 'tool_observed',
  FILE_WRITTEN: 'file_written',
  PATCH_APPLIED: 'patch_applied',
  APPROVAL_REQUESTED: 'approval_requested',
  APPROVAL_GRANTED: 'approval_granted',
  APPROVAL_DECLINED: 'approval_declined',
  EXECUTION_STARTED: 'execution_started',
  EXECUTION_COMPLETED: 'execution_completed',
  EXECUTION_FAILED: 'execution_failed',
  TEST_RUN_STARTED: 'test_run_started',
  TEST_RUN_COMPLETED: 'test_run_completed',
  VALIDATION_RUN: 'validation_run',
  ARTIFACT_GENERATED: 'artifact_generated',
  BUDGET_WARNED: 'budget_warned',
  BUDGET_EXHAUSTED: 'budget_exhausted',
  REPAIR_PROBLEM_RECEIVED: 'repair_problem_received',
  AGENT_SUMMARY_PRODUCED: 'agent_summary_produced',
  REPRODUCTION_RESULT: 'reproduction_result',
  DIAGNOSIS_PRODUCED: 'diagnosis_produced',
  PATCH_PROPOSED: 'patch_proposed',
  REPAIR_REPORT_GENERATED: 'repair_report_generated',
  WORKFLOW_COMPLETED: 'workflow_completed',
  WORKFLOW_FAILED: 'workflow_failed',
  SESSION_AUTO_ARCHIVED: 'session_auto_archived',
} as const;
export type EventKind = (typeof EventKind)[keyof typeof EventKind];

export interface WorkspaceEvent {
  readonly id: string;
  readonly session_id: string;
  readonly ts: string;
  readonly step: number;
  readonly kind: EventKind;
  readonly actor_type: ActorType;
  readonly payload: Record<string, unknown>;
  readonly prev_event_id: string | null;
}

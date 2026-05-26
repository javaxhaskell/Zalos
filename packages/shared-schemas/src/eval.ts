import type { SessionStatus } from './common.js';

export const EvalKind = {
  AUTHOR: 'author',
  REPAIR: 'repair',
  ADVERSARIAL: 'adversarial',
} as const;
export type EvalKind = (typeof EvalKind)[keyof typeof EvalKind];

export interface GoldenOutputSpec {
  readonly path: string;
  readonly golden: string;
  readonly comparison: 'row_aligned_on_primary_key' | 'exact';
  readonly primary_key: string | null;
  readonly tolerance: Record<string, number>;
}

export interface AuthorScenario {
  readonly id: string;
  readonly kind: 'author';
  readonly template: string;
  readonly input_files: readonly string[];
  readonly workflow_description: string;
  readonly expected_output_files: readonly GoldenOutputSpec[];
  readonly expected_validation_layers: readonly string[];
  readonly expected_terminal_status: SessionStatus;
}

export interface ExpectedDiagnosis {
  readonly suspected_file: string;
  readonly suspected_lines_range: readonly [number, number];
  readonly root_cause_substring_any_of: readonly string[];
}

export interface ExpectedPatch {
  readonly file: string;
  readonly hunks_count: number;
  readonly must_not_modify_files: readonly string[];
}

export interface ExpectedAfterFix {
  readonly pytest_passed: number;
  readonly pytest_failed: number;
  readonly golden_diff_on_column: string | null;
  readonly golden_diff_count: number;
}

export interface RepairScenario {
  readonly id: string;
  readonly kind: 'repair';
  readonly fixture: string;
  readonly problem_report_path: string;
  readonly expected_reproduction_method: 'pytest' | 'sample_run';
  readonly expected_failing_test: string | null;
  readonly expected_diagnosis: ExpectedDiagnosis;
  readonly expected_patch: ExpectedPatch;
  readonly expected_after_fix: ExpectedAfterFix;
  readonly expected_terminal_status: SessionStatus;
}

export interface AdversarialScenario {
  readonly id: string;
  readonly kind: 'adversarial';
  readonly subkind: 'author_with_injection_in_csv';
  readonly template: string;
  readonly injection_payload: string;
  readonly injection_location: string;
  readonly workflow_description: string;
  readonly expected_behaviour: readonly string[];
  readonly expected_terminal_status: SessionStatus;
}

export type EvalScenario = AuthorScenario | RepairScenario | AdversarialScenario;

export interface EvalRunResult {
  readonly scenario_id: string;
  readonly passed: boolean;
  readonly latency_ms: number;
  readonly cost_usd: number;
  readonly failure_reason: string | null;
  readonly diff_path: string | null;
}

export interface EvalRunSummary {
  readonly id: string;
  readonly started_at: string;
  readonly completed_at: string | null;
  readonly total: number;
  readonly passed: number;
  readonly failed: number;
  readonly per_tag: Record<string, { readonly passed: number; readonly failed: number }>;
  readonly results: readonly EvalRunResult[];
}

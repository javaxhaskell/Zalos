import type { Severity } from './common.js';

// ---------------------------------------------------------------------------
// Author flow
// ---------------------------------------------------------------------------

export interface ColumnProfile {
  readonly name: string;
  readonly dtype: string;
  readonly null_rate: number;
  readonly sample_values: readonly string[];
  readonly ambiguity_note: string | null;
}

export interface FileProfile {
  readonly file_id: string;
  readonly filename: string;
  readonly row_count: number;
  readonly encoding: string;
  readonly columns: readonly ColumnProfile[];
  readonly sheet_name: string | null;
}

export interface BusinessRule {
  readonly name: string;
  readonly description: string;
  readonly severity: Severity;
}

export interface AuthorRequirements {
  readonly session_id: string;
  readonly description: string;
  readonly input_file_ids: readonly string[];
  readonly expected_output_columns: readonly ColumnProfile[];
  readonly business_rules: readonly BusinessRule[];
  readonly edge_cases: readonly string[];
  readonly validation_checks: readonly string[];
  readonly template_used: string | null;
}

// ---------------------------------------------------------------------------
// Repair flow
// ---------------------------------------------------------------------------

export const ProblemClassification = {
  TEST_FAILURE: 'test_failure',
  RUNTIME_ERROR: 'runtime_error',
  WRONG_OUTPUT: 'wrong_output',
  PERFORMANCE: 'performance',
  OTHER: 'other',
} as const;
export type ProblemClassification =
  (typeof ProblemClassification)[keyof typeof ProblemClassification];

export interface RepairProblem {
  readonly session_id: string;
  readonly report_text: string;
  readonly classification: ProblemClassification;
  readonly confidence: number;
}

export interface AgentSummary {
  readonly session_id: string;
  readonly purpose: string;
  readonly inputs: readonly string[];
  readonly outputs: readonly string[];
  readonly entry_point: string;
  readonly dependencies: readonly string[];
}

export const ReproductionMethod = {
  PYTEST: 'pytest',
  SAMPLE_RUN: 'sample_run',
} as const;
export type ReproductionMethod = (typeof ReproductionMethod)[keyof typeof ReproductionMethod];

export interface ReproductionResult {
  readonly id: string;
  readonly session_id: string;
  readonly reproduced: boolean;
  readonly method: ReproductionMethod;
  readonly failing_test: string | null;
  readonly error_excerpt: string | null;
  readonly observation_id: string | null;
}

export interface Diagnosis {
  readonly id: string;
  readonly session_id: string;
  readonly suspected_file: string;
  readonly suspected_lines: readonly [number, number];
  readonly root_cause: string;
  readonly severity: Severity;
  readonly fix_risk: Severity;
  readonly confidence: number;
}

export interface PatchProposal {
  readonly id: string;
  readonly diagnosis_id: string;
  readonly file: string;
  readonly unified_diff: string;
  readonly rationale: string;
}

// ---------------------------------------------------------------------------
// Q&A
// ---------------------------------------------------------------------------

export const QuestionStyle = {
  FREE_TEXT: 'free_text',
  RADIO: 'radio',
  SELECT_COLUMN: 'select_column',
} as const;
export type QuestionStyle = (typeof QuestionStyle)[keyof typeof QuestionStyle];

export interface PendingQuestion {
  readonly id: string;
  readonly session_id: string;
  readonly text: string;
  readonly style: QuestionStyle;
  readonly options: readonly string[];
}

export interface QuestionAnswer {
  readonly question_id: string;
  readonly answer: string;
}

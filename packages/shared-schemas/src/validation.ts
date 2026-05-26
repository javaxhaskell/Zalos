export const ValidationLayer = {
  SCHEMA: 'schema',
  REQUIRED_COLUMNS: 'required_columns',
  BUSINESS_RULES: 'business_rules',
  ROW_LEVEL: 'row_level',
  GOLDEN_OUTPUT: 'golden_output',
  GENERATED_PYTEST: 'generated_pytest',
} as const;
export type ValidationLayer = (typeof ValidationLayer)[keyof typeof ValidationLayer];

export interface ValidationCheck {
  readonly layer: ValidationLayer;
  readonly name: string;
  readonly passed: boolean;
  readonly evidence: string;
  readonly detail: string | null;
  readonly hint_if_failed: string | null;
}

export interface ValidationReport {
  readonly session_id: string;
  readonly generated_at: string;
  readonly overall_passed: boolean;
  readonly checks: readonly ValidationCheck[];
}

export interface FilesChangedEntry {
  readonly file: string;
  readonly hunks_count: number;
  readonly diff_hash: string;
  readonly summary: string;
}

export interface TestRunSummary {
  readonly passed_count: number;
  readonly failed_count: number;
  readonly total_count: number;
  readonly failing_tests: readonly string[];
}

export interface RepairReport {
  readonly session_id: string;
  readonly generated_at: string;
  readonly problem: string;
  readonly reproduction: string;
  readonly diagnosis: string;
  readonly files_changed: readonly FilesChangedEntry[];
  readonly validation_before: TestRunSummary;
  readonly validation_after: TestRunSummary;
  readonly golden_diff_zero: boolean | null;
  readonly remaining_risks: readonly string[];
  readonly next_steps: readonly string[];
}

export const ArtifactType = {
  UPLOADED_FILE: 'uploaded_file',
  GENERATED_CODE: 'generated_code',
  OUTPUT_FILE: 'output_file',
  WORKFLOW_ROW_OUTPUT: 'workflow_row_output',
  WORKFLOW_REPORT: 'workflow_report',
  SYSTEM_VALIDATION_REPORT: 'system_validation_report',
  VALIDATION_REPORT: 'validation_report',
  REPAIR_REPORT: 'repair_report',
  ARCHIVE: 'archive',
} as const;
export type ArtifactType = (typeof ArtifactType)[keyof typeof ArtifactType];

export interface Artifact {
  readonly id: string;
  readonly session_id: string;
  readonly type: ArtifactType;
  readonly path: string;
  readonly hash: string;
  readonly size_bytes: number;
  readonly created_at: string;
}

export interface UploadedFile {
  readonly id: string;
  readonly session_id: string;
  readonly filename: string;
  readonly mime: string;
  readonly size_bytes: number;
  readonly hash_sha256: string;
  readonly storage_path: string;
  readonly uploaded_at: string;
}

export interface ExecutionObservation {
  readonly invocation_id: string;
  readonly success: boolean;
  readonly exit_code: number | null;
  readonly stdout_excerpt: string;
  readonly stderr_excerpt: string;
  readonly files_written: readonly string[];
  readonly latency_ms: number;
  readonly truncated: boolean;
  readonly overflow_log_path: string | null;
}

export interface PerTestResult {
  readonly name: string;
  readonly status: 'passed' | 'failed' | 'skipped' | 'error';
  readonly humanised_name: string;
  readonly latency_ms: number;
  readonly output_excerpt: string | null;
}

export interface TestResults {
  readonly invocation_id: string;
  readonly passed_count: number;
  readonly failed_count: number;
  readonly skipped_count: number;
  readonly error_count: number;
  readonly total_count: number;
  readonly summary_line: string;
  readonly per_test: readonly PerTestResult[];
  readonly raw_output_excerpt: string;
}

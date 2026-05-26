import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ApiError } from "@/lib/api-client";

/**
 * Render the backend's typed error envelope.
 *
 * The shape is locked in apps/api/src/agentforge/api/errors.py:
 *   { error_code: str, message: str, technical_detail: str | null,
 *     session_id: str | null, details: dict }
 *
 * On unknown errors we fall back to the raw message so the user sees
 * *something* — silently swallowing failures violates INV-6's spirit
 * (every state change must be visible).
 */
interface BackendErrorEnvelope {
  readonly error_code?: string;
  readonly message?: string;
  readonly technical_detail?: string | null;
  readonly details?: Record<string, unknown>;
}

export interface ErrorBannerProps {
  readonly error: unknown;
}

export function ErrorBanner({ error }: ErrorBannerProps) {
  const { title, message, detail } = unpack(error);
  return (
    <Alert variant="error">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
      {detail ? (
        <details className="mt-2 cursor-pointer text-xs">
          <summary>Technical detail</summary>
          <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">{detail}</pre>
        </details>
      ) : null}
    </Alert>
  );
}

function unpack(err: unknown): { title: string; message: string; detail: string | null } {
  if (err instanceof ApiError) {
    const envelope = err.envelope as
      | BackendErrorEnvelope
      | { detail?: BackendErrorEnvelope }
      | null;
    // FastAPI's HTTPException wraps the body in {detail: ...}; the
    // structured handler in errors.py returns a flat shape.
    const inner =
      envelope && typeof envelope === "object" && "detail" in envelope
        ? (envelope as { detail?: BackendErrorEnvelope }).detail ?? {}
        : ((envelope as BackendErrorEnvelope | null) ?? {});

    if (inner.error_code === "api_unreachable") {
      return {
        title: "API not running",
        message:
          inner.message ??
          "Start the AgentForge API with `make up` or `pnpm dev` from the repo root.",
        detail: inner.technical_detail ?? null,
      };
    }
    if (inner.error_code === "db_schema_outdated") {
      return {
        title: "Database needs migration",
        message:
          inner.message ?? "Run `make migrate` from the repo root, then restart the API.",
        detail: inner.technical_detail ?? null,
      };
    }
    if (!inner.error_code && (err.status === 500 || err.status === 502)) {
      return {
        title: "Backend unavailable",
        message:
          "The API returned an error without details. If you started only the web app, run `make up` or `pnpm dev` from the repo root.",
        detail: inner.technical_detail ?? null,
      };
    }
    return {
      title: humaniseCode(inner.error_code) ?? `Request failed (${err.status})`,
      message: inner.message ?? err.message,
      detail: inner.technical_detail ?? null,
    };
  }
  if (err instanceof Error) {
    return { title: "Something went wrong", message: err.message, detail: null };
  }
  return { title: "Something went wrong", message: String(err), detail: null };
}

function humaniseCode(code: string | undefined): string | null {
  if (!code) return null;
  const map: Record<string, string> = {
    malformed_csv: "We couldn't read that file",
    unsupported_file_type: "Unsupported file type",
    file_too_large: "File too large",
    upload_limit_exceeded: "Upload limit reached",
    missing_required_columns: "Missing required columns",
    ambiguous_schema: "Ambiguous schema",
    tool_not_registered: "Unknown tool",
    validation_loop_exhausted: "Couldn't complete validation",
    generated_code_failed: "The generated code failed to run",
    test_failed: "A check failed",
    command_timeout: "The agent's code timed out",
    repair_cannot_reproduce: "We couldn't reproduce the failure",
    budget_exhausted_tokens: "Out of token budget",
    budget_exhausted_tool_calls: "Out of tool-call budget",
    budget_exhausted_steps: "Out of step budget",
    budget_exhausted_wall_time: "Out of wall-clock budget",
    budget_exhausted_file_count: "Out of file budget",
    approval_declined: "Approval declined",
    sandbox_crash: "The sandbox crashed",
    approval_request_not_found: "Approval not found",
    approval_already_decided: "Approval already decided",
    session_not_found: "Session not found",
    workflow_interrupted: "Workflow interrupted",
    unknown: "Unexpected error",
  };
  return map[code] ?? code;
}

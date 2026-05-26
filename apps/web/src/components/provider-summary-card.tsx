"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { WorkspaceEvent } from "@/lib/api-client";
import {
  deriveAuthorWorkflowContext,
  humaniseCustomWorkflowTypeLabel,
} from "@/lib/ux-language";

/**
 * LLM provenance / build-path card for successful Author sessions.
 */
export interface ProviderSummaryCardProps {
  readonly events: ReadonlyArray<WorkspaceEvent>;
  readonly hideTokenUsage?: boolean;
}

interface ProviderState {
  provider: string | null;
  model: string | null;
  baseUrl: string | null;
  modelCallCount: number;
  tokensUsed: number;
  totalLatencyMs: number;
  completionVia: string | null;
  workflowType: string | null;
  modelAuthoredFiles: string[];
  validationPassed: boolean;
}

function isAiAuthoredWorkflowBuild(
  completionVia: string | null,
  modelCallCount: number,
  modelAuthoredFiles: string[],
): boolean {
  if (modelCallCount <= 0) return false;
  if (modelAuthoredFiles.length === 0) return false;
  return completionVia === "ai_authored_workflow_build";
}

export function ProviderSummaryCard({
  events,
  hideTokenUsage = false,
}: ProviderSummaryCardProps) {
  const state = useMemo(() => deriveProviderState(events), [events]);
  const workflowContext = useMemo(
    () => deriveAuthorWorkflowContext(events),
    [events],
  );

  if (state.modelCallCount === 0 && state.completionVia === null) {
    return null;
  }

  if (
    !isAiAuthoredWorkflowBuild(
      state.completionVia,
      state.modelCallCount,
      state.modelAuthoredFiles,
    )
  ) {
    return null;
  }

  const workflowType =
    state.workflowType ?? workflowContext.workflowType ?? "custom_finance_workflow";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Build path</span>
          <Badge variant="success">AI-authored workflow build</Badge>
        </CardTitle>
        <CardDescription>
          The model authored the output contract, reviewed contract, agent code,
          and tests. Deterministic validators checked the result before completion.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Field
            label="Workflow type"
            value={humaniseCustomWorkflowTypeLabel(workflowType)}
          />
          {state.provider ? (
            <Field
              label="AI service"
              value={humaniseProviderLabel(state.provider)}
            />
          ) : null}
          {state.model ? <Field label="Model" value={state.model} mono /> : null}
          <Field label="Model calls" value={String(state.modelCallCount)} />
          {hideTokenUsage ? null : (
            <Field label="Tokens used" value={String(state.tokensUsed)} />
          )}
          <Field
            label="Validation"
            value={state.validationPassed ? "Passed" : "Unknown"}
          />
        </dl>
        {state.modelAuthoredFiles.length ? (
          <div className="mt-4">
            <p className="text-sm font-medium text-slate-900">Model-authored files</p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs font-mono text-slate-700">
              {state.modelAuthoredFiles.map((path) => (
                <li key={path}>{path}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function humaniseProviderLabel(provider: string): string {
  const normalized = provider.trim().toLowerCase();
  if (normalized === "deepseek") return "AI model connected";
  if (normalized === "anthropic") return "AI model connected";
  if (normalized === "ollama") return "Local authoring engine";
  return "Authoring engine ready";
}

function Field({
  label,
  value,
  mono = false,
}: {
  readonly label: string;
  readonly value: string;
  readonly mono?: boolean;
}) {
  return (
    <>
      <dt className="text-slate-500">{label}</dt>
      <dd className={mono ? "font-mono text-xs text-slate-900" : "text-slate-900"}>
        {value}
      </dd>
    </>
  );
}

function deriveProviderState(events: ReadonlyArray<WorkspaceEvent>): ProviderState {
  let provider: string | null = null;
  let model: string | null = null;
  let baseUrl: string | null = null;
  let modelCallCount = 0;
  let tokensUsed = 0;
  let totalLatencyMs = 0;
  let completionVia: string | null = null;
  let workflowType: string | null = null;
  let modelAuthoredFiles: string[] = [];
  let validationPassed = false;

  for (const ev of events) {
    if (ev.kind === "model_called") {
      modelCallCount += 1;
      const payload = ev.payload as Record<string, unknown>;
      if (provider === null && typeof payload.provider === "string") {
        provider = payload.provider;
      }
      if (model === null && typeof payload.model === "string") {
        model = payload.model;
      }
      if (
        baseUrl === null &&
        typeof payload.base_url === "string" &&
        payload.base_url !== ""
      ) {
        baseUrl = payload.base_url;
      }
      const latency = payload.latency_ms;
      if (typeof latency === "number") totalLatencyMs += latency;
      const usage = payload.usage as Record<string, unknown> | undefined;
      if (usage) {
        const total = usage.total_tokens;
        if (typeof total === "number" && total > 0) {
          tokensUsed += total;
        } else {
          const input = usage.input_tokens;
          const output = usage.output_tokens;
          if (typeof input === "number") tokensUsed += input;
          if (typeof output === "number") tokensUsed += output;
        }
      }
    }
    if (ev.kind === "decision_input") {
      const payload = ev.payload as Record<string, unknown>;
      if (payload.kind === "model_authoring_provenance") {
        const files = payload.model_contributed_files;
        if (Array.isArray(files)) {
          modelAuthoredFiles = files.filter((item): item is string => typeof item === "string");
        }
      }
    }
    if (ev.kind === "validation_run") {
      const payload = ev.payload as Record<string, unknown>;
      if (payload.overall_passed === true) {
        validationPassed = true;
      }
    }
    if (ev.kind === "workflow_completed") {
      const payload = ev.payload as Record<string, unknown>;
      if (typeof payload.via === "string") {
        completionVia = payload.via;
      }
      if (typeof payload.workflow_type === "string") {
        workflowType = payload.workflow_type;
      }
    }
  }

  const highlighted = [
    "generated/agent.py",
    "generated/tests/test_agent.py",
    "generated/author_output_contract.json",
  ];
  const displayFiles = highlighted.filter((path) => modelAuthoredFiles.includes(path));
  const extraFiles = modelAuthoredFiles.filter((path) => !displayFiles.includes(path));
  modelAuthoredFiles = [...displayFiles, ...extraFiles];

  return {
    provider,
    model,
    baseUrl,
    modelCallCount,
    tokensUsed,
    totalLatencyMs,
    completionVia,
    workflowType,
    modelAuthoredFiles,
    validationPassed,
  };
}

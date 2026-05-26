"use client";

import { useEffect, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ApiError, answerSession, runSession } from "@/lib/api-client";
import {
  USER_QUESTION_PANEL_COPY,
  defaultDateFormatClarificationAnswer,
  formatClarificationSampleDates,
  humaniseAgentQuestion,
} from "@/lib/ux-language";

/**
 * Q&A pause UI for ``question_asked`` events. When the agent loop pauses on
 * ``paused_user``, the wizard surfaces this panel; the user types their
 * answer; we POST ``/sessions/{id}/answer`` then ``/run`` to continue.
 */
export interface UserQuestionPanelProps {
  readonly sessionId: string;
  readonly questionEventId: string;
  readonly questionText: string;
  readonly affectedColumns?: ReadonlyArray<string>;
  readonly sampleValues?: ReadonlyArray<{
    readonly column: string;
    readonly values: ReadonlyArray<string>;
  }>;
  readonly clarificationKind?: string;
  readonly onAnswered?: () => void;
}

export function UserQuestionPanel({
  sessionId,
  questionEventId,
  questionText,
  affectedColumns = [],
  sampleValues = [],
  clarificationKind,
  onAnswered,
}: UserQuestionPanelProps) {
  const presetAnswer = defaultDateFormatClarificationAnswer(
    clarificationKind,
    sampleValues,
  );
  const [answer, setAnswer] = useState(presetAnswer);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const isDateClarification =
    clarificationKind === "date_format" || sampleValues.length > 0;
  const copy = USER_QUESTION_PANEL_COPY;
  const exampleDates = formatClarificationSampleDates(sampleValues);

  useEffect(() => {
    setAnswer(
      defaultDateFormatClarificationAnswer(clarificationKind, sampleValues),
    );
    setError(null);
  }, [questionEventId, clarificationKind, sampleValues]);

  async function onSubmit() {
    const trimmed = answer.trim();
    if (trimmed.length === 0) {
      setError(
        new Error(
          isDateClarification
            ? copy.dateEmptyAnswerError
            : copy.emptyAnswerError,
        ),
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await answerSession(sessionId, trimmed);
      try {
        await runSession(sessionId, {}, { timeoutMs: 120_000 });
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          // Another run is already in flight — polling will pick up progress.
        } else {
          throw err;
        }
      }
      onAnswered?.();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{copy.title}</CardTitle>
        <CardDescription>
          {isDateClarification ? copy.dateDescription : copy.description}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm leading-relaxed text-slate-800">
          {isDateClarification
            ? copy.dateQuestionText
            : humaniseAgentQuestion(questionText)}
        </p>
        {exampleDates.length > 0 ? (
          <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2.5">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              {copy.examplesLabel}
            </p>
            <p className="mt-1 text-sm text-slate-800">{exampleDates}</p>
          </div>
        ) : null}
        {affectedColumns.length > 0 && sampleValues.length === 0 ? (
          <p className="text-xs text-slate-600">
            Affected columns: {affectedColumns.join(", ")}
          </p>
        ) : null}
        <div>
          <label
            htmlFor={`answer-${questionEventId}`}
            className="block text-sm font-medium text-slate-700"
          >
            {isDateClarification ? copy.dateAnswerLabel : copy.answerLabel}
          </label>
          <textarea
            id={`answer-${questionEventId}`}
            className="mt-1.5 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500"
            rows={3}
            value={answer}
            onChange={(e) => {
              setAnswer(e.target.value);
              if (error) setError(null);
            }}
            placeholder={
              isDateClarification
                ? copy.dateAnswerPlaceholder
                : copy.answerPlaceholder
            }
          />
        </div>
        {error ? <ErrorBanner error={error} /> : null}
      </CardContent>
      <CardFooter className="justify-end">
        <Button
          type="button"
          variant="primary"
          disabled={busy}
          onClick={onSubmit}
        >
          {busy ? copy.submittingButton : copy.submitButton}
        </Button>
      </CardFooter>
    </Card>
  );
}

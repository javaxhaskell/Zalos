"use client";

import { Check, Copy } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { cn } from "@/lib/utils";

const COPIED_FEEDBACK_MS = 1500;

function useCopySessionId(sessionId: string) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(sessionId);
      setCopied(true);
      if (resetTimerRef.current !== null) {
        window.clearTimeout(resetTimerRef.current);
      }
      resetTimerRef.current = window.setTimeout(() => {
        setCopied(false);
        resetTimerRef.current = null;
      }, COPIED_FEEDBACK_MS);
    } catch {
      // Clipboard access may be denied; leave UI unchanged.
    }
  }, [sessionId]);

  return { copied, onCopy };
}

export interface CopySessionIdButtonProps {
  readonly sessionId: string;
  readonly className?: string;
}

/**
 * Compact copy icon for session IDs with brief "Copied" feedback.
 */
export function CopySessionIdButton({
  sessionId,
  className,
}: CopySessionIdButtonProps) {
  const { copied, onCopy } = useCopySessionId(sessionId);

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        void onCopy();
      }}
      aria-label={copied ? "Copied session ID" : "Copy session ID"}
      title={copied ? "Copied" : "Copy session ID"}
      className={cn(
        "inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-slate-400",
        "hover:bg-slate-100 hover:text-slate-600",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-slate-400",
        className,
      )}
    >
      {copied ? (
        <Check aria-hidden className="h-3.5 w-3.5 text-emerald-600" />
      ) : (
        <Copy aria-hidden className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

export interface CopyableSessionIdProps {
  readonly sessionId: string;
  readonly className?: string;
  readonly inline?: boolean;
}

/**
 * Click-to-copy session ID with hover affordance and brief feedback.
 */
export function CopyableSessionId({
  sessionId,
  className,
  inline = false,
}: CopyableSessionIdProps) {
  const { copied, onCopy } = useCopySessionId(sessionId);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLButtonElement>) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        void onCopy();
      }
    },
    [onCopy],
  );

  return (
    <button
      type="button"
      onClick={() => void onCopy()}
      onKeyDown={onKeyDown}
      aria-label="Copy session ID"
      title={copied ? "Copied" : "Click to copy"}
      className={cn(
        "font-mono break-all text-left",
        "cursor-pointer rounded-sm px-0.5 -mx-0.5",
        "hover:bg-slate-100 hover:underline hover:underline-offset-2",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-400",
        inline && "inline",
        className,
      )}
    >
      {sessionId}
      <span className="sr-only">{copied ? " — Copied" : ""}</span>
    </button>
  );
}

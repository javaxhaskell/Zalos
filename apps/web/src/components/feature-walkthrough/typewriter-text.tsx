"use client";

import { cn } from "@/lib/utils";

import {
  useTypewriterText,
  type UseTypewriterTextOptions,
} from "./use-typewriter-text";

export interface TypewriterTextProps
  extends Omit<UseTypewriterTextOptions, "text"> {
  readonly text: string;
  readonly className?: string;
  readonly caretClassName?: string;
}

export function TypewriterText({
  text,
  enabled,
  runKey,
  startDelayMs,
  msPerChar,
  onComplete,
  className,
  caretClassName,
}: TypewriterTextProps) {
  const { displayText, isTyping } = useTypewriterText({
    text,
    enabled,
    runKey,
    startDelayMs,
    msPerChar,
    onComplete,
  });

  return (
    <span className={className}>
      {displayText}
      {isTyping ? (
        <span
          aria-hidden
          className={cn(
            "ml-px inline-block w-px translate-y-px animate-pulse bg-slate-400/80",
            caretClassName,
          )}
          style={{ height: "0.85em" }}
        />
      ) : null}
    </span>
  );
}

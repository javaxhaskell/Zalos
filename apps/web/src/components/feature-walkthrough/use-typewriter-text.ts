"use client";

import { useEffect, useRef, useState } from "react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";

export interface UseTypewriterTextOptions {
  readonly text: string;
  readonly enabled: boolean;
  readonly runKey: number;
  readonly startDelayMs?: number;
  readonly msPerChar?: number;
  readonly onComplete?: () => void;
}

export interface UseTypewriterTextResult {
  readonly displayText: string;
  readonly isTyping: boolean;
  readonly isComplete: boolean;
  readonly progress: number;
}

export function useTypewriterText({
  text,
  enabled,
  runKey,
  startDelayMs = 0,
  msPerChar = 22,
  onComplete,
}: UseTypewriterTextOptions): UseTypewriterTextResult {
  const reducedMotion = usePrefersReducedMotion();
  const [visibleCount, setVisibleCount] = useState(0);
  const onCompleteRef = useRef(onComplete);

  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (!enabled) {
      setVisibleCount(0);
      return;
    }

    if (reducedMotion) {
      setVisibleCount(text.length);
      onCompleteRef.current?.();
      return;
    }

    setVisibleCount(0);
    let frameId = 0;
    let completed = false;
    let startTime: number | null = null;

    const tick = (timestamp: number) => {
      if (startTime === null) {
        startTime = timestamp;
      }

      const elapsed = timestamp - startTime - startDelayMs;
      if (elapsed < 0) {
        frameId = requestAnimationFrame(tick);
        return;
      }

      const nextCount = Math.min(text.length, Math.floor(elapsed / msPerChar));
      setVisibleCount(nextCount);

      if (nextCount < text.length) {
        frameId = requestAnimationFrame(tick);
        return;
      }

      if (!completed) {
        completed = true;
        onCompleteRef.current?.();
      }
    };

    frameId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameId);
  }, [enabled, msPerChar, reducedMotion, runKey, startDelayMs, text]);

  const isComplete = visibleCount >= text.length;
  const isTyping = enabled && !reducedMotion && visibleCount > 0 && !isComplete;
  const progress = text.length === 0 ? 1 : visibleCount / text.length;

  return {
    displayText: text.slice(0, visibleCount),
    isTyping,
    isComplete,
    progress,
  };
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

import { useTypewriterText } from "./use-typewriter-text";

const MS_PER_CHAR = 20;

export interface AnimatedPlanListProps {
  readonly items: readonly string[];
  readonly isActive: boolean;
  readonly idleProgressPercent?: number;
  readonly completeProgressPercent?: number;
  readonly onProgressChange?: (percent: number) => void;
  readonly className?: string;
}

function useAnimationRun(isActive: boolean): number {
  const [runKey, setRunKey] = useState(0);
  const wasActiveRef = useRef(false);

  useEffect(() => {
    if (isActive && !wasActiveRef.current) {
      setRunKey((current) => current + 1);
    }
    wasActiveRef.current = isActive;
  }, [isActive]);

  return runKey;
}

interface PlanListItemProps {
  readonly index: number;
  readonly text: string;
  readonly isActive: boolean;
  readonly runKey: number;
  readonly isTyping: boolean;
  readonly isComplete: boolean;
  readonly onComplete: () => void;
  readonly onProgress: (progress: number) => void;
}

function PlanListItem({
  index,
  text,
  isActive,
  runKey,
  isTyping,
  isComplete,
  onComplete,
  onProgress,
}: PlanListItemProps) {
  const reducedMotion = usePrefersReducedMotion();
  const showFullText =
    !isActive || isComplete || (reducedMotion && isActive);

  return (
    <motion.li
      className="flex gap-1.5 rounded-md border border-slate-200/80 bg-white px-1.5 py-0.5"
      initial={false}
      animate={
        isActive
          ? { opacity: 1, x: 0, filter: "blur(0px)" }
          : { opacity: 0.45, x: 6, filter: "blur(1px)" }
      }
      transition={{
        duration: 0.28,
        delay: isActive && isTyping ? 0.04 : 0,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <span className="mt-px flex size-3 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[8px] font-semibold text-slate-600">
        {index + 1}
      </span>
      <span className="text-[10px] leading-snug text-slate-700">
        {showFullText ? (
          text
        ) : isTyping ? (
          <PlanItemTypewriter
            key={`${runKey}-${index}`}
            text={text}
            enabled={isActive && isTyping}
            runKey={runKey}
            onComplete={onComplete}
            onProgress={onProgress}
          />
        ) : (
          <span aria-hidden className="inline-block min-h-[1em]">
            {"\u00A0"}
          </span>
        )}
      </span>
    </motion.li>
  );
}

interface PlanItemTypewriterProps {
  readonly text: string;
  readonly enabled: boolean;
  readonly runKey: number;
  readonly onComplete: () => void;
  readonly onProgress: (progress: number) => void;
}

function PlanItemTypewriter({
  text,
  enabled,
  runKey,
  onComplete,
  onProgress,
}: PlanItemTypewriterProps) {
  const { displayText, isTyping, progress } = useTypewriterText({
    text,
    enabled,
    runKey,
    msPerChar: MS_PER_CHAR,
    onComplete,
  });

  useEffect(() => {
    onProgress(progress);
  }, [onProgress, progress]);

  return (
    <span className="inline">
      {displayText}
      {isTyping ? (
        <span
          aria-hidden
          className="ml-px inline-block w-px translate-y-px animate-pulse bg-slate-400/80"
          style={{ height: "0.85em" }}
        />
      ) : null}
    </span>
  );
}

export function AnimatedPlanList({
  items,
  isActive,
  idleProgressPercent = 24,
  completeProgressPercent = 68,
  onProgressChange,
  className,
}: AnimatedPlanListProps) {
  const reducedMotion = usePrefersReducedMotion();
  const runKey = useAnimationRun(isActive);
  const [typingIndex, setTypingIndex] = useState(0);
  const [itemProgress, setItemProgress] = useState<readonly number[]>(() =>
    items.map(() => 0),
  );

  useEffect(() => {
    setTypingIndex(0);
    setItemProgress(items.map(() => 0));
  }, [items, runKey]);

  const handleItemComplete = useCallback(
    (index: number) => {
      setItemProgress((current) => {
        const next = [...current];
        next[index] = 1;
        return next;
      });
      setTypingIndex(index + 1);
    },
    [],
  );

  const handleItemProgress = useCallback((index: number, progress: number) => {
    setItemProgress((current) => {
      if (current[index] === progress) {
        return current;
      }
      const next = [...current];
      next[index] = progress;
      return next;
    });
  }, []);

  const progressPercent = useMemo(() => {
    if (!isActive) {
      return idleProgressPercent;
    }

    if (reducedMotion) {
      return completeProgressPercent;
    }

    const averageProgress =
      itemProgress.reduce((sum, value) => sum + value, 0) / items.length;
    const range = completeProgressPercent - idleProgressPercent;

    return idleProgressPercent + averageProgress * range;
  }, [
    completeProgressPercent,
    idleProgressPercent,
    isActive,
    itemProgress,
    items.length,
    reducedMotion,
  ]);

  useEffect(() => {
    onProgressChange?.(progressPercent);
  }, [onProgressChange, progressPercent]);

  return (
    <ul className={cn("min-h-0 flex-1 space-y-0.5 overflow-y-auto", className)}>
      {items.map((item, index) => (
        <PlanListItem
          key={item}
          index={index}
          text={item}
          isActive={isActive}
          runKey={runKey}
          isTyping={index === typingIndex}
          isComplete={index < typingIndex}
          onComplete={() => handleItemComplete(index)}
          onProgress={(progress) => handleItemProgress(index, progress)}
        />
      ))}
    </ul>
  );
}

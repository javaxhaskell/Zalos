"use client";

import type { ReactNode } from "react";

import {
  LiveProcessBadge,
  sessionStatusToTone,
} from "@/components/live-process-badge";
import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

export interface StatusBadgeTransitionProps {
  /** Change this when status changes to re-trigger the entrance. */
  readonly statusKey: string;
  readonly children: ReactNode;
  readonly className?: string;
}

/**
 * Session header status pill with a short entrance transition.
 */
export function StatusBadgeTransition({
  statusKey,
  children,
  className,
}: StatusBadgeTransitionProps) {
  const reduced = usePrefersReducedMotion();
  const tone = sessionStatusToTone(statusKey);
  const label = typeof children === "string" ? children : String(children);

  return (
    <span
      key={statusKey}
      className={cn(!reduced && "animate-status-in inline-flex", className)}
    >
      <LiveProcessBadge tone={tone} label={label} />
    </span>
  );
}

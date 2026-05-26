"use client";

import { cn } from "@/lib/utils";

export type LiveProcessTone = "running" | "saved" | "attention" | "failed" | "complete";

export interface LiveProcessBadgeProps {
  readonly tone?: LiveProcessTone;
  readonly label?: string;
  /** Screen-reader label when the visible label is hidden (dot-only rows). */
  readonly ariaLabel?: string;
  readonly className?: string;
  /** Pill with border (default) or glowing dot only. */
  readonly variant?: "pill" | "dot";
}

const pillStyles: Record<LiveProcessTone, string> = {
  running:
    "bg-blue-50 text-blue-900 ring-1 ring-blue-200/90 shadow-[0_0_16px_rgba(59,130,246,0.35)]",
  saved:
    "bg-slate-50 text-slate-700 ring-1 ring-slate-200/90 shadow-[0_0_10px_rgba(100,116,139,0.12)]",
  attention:
    "bg-orange-50 text-orange-900 ring-1 ring-orange-200/90 shadow-[0_0_14px_rgba(249,115,22,0.28)]",
  failed:
    "bg-red-50 text-red-900 ring-1 ring-red-200/90 shadow-[0_0_14px_rgba(239,68,68,0.28)]",
  complete:
    "bg-emerald-50 text-emerald-900 ring-1 ring-emerald-200/90 shadow-[0_0_12px_rgba(16,185,129,0.22)]",
};

const dotStyles: Record<LiveProcessTone, string> = {
  running: "bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.75)] animate-pulse",
  saved: "bg-slate-400",
  attention: "bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.55)]",
  failed: "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.55)]",
  complete: "bg-emerald-600 shadow-[0_0_8px_rgba(16,185,129,0.45)]",
};

/** Dot-only palette for compact lists (recent sessions). */
const dotOnlyStyles: Record<LiveProcessTone, string> = {
  running: "bg-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.8)] animate-pulse",
  saved: "bg-slate-400 shadow-[0_0_8px_rgba(148,163,184,0.55)]",
  attention: "bg-orange-500 shadow-[0_0_10px_rgba(249,115,22,0.8)]",
  failed: "bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.7)]",
  complete: "bg-emerald-500 shadow-[0_0_10px_rgba(16,185,129,0.7)]",
};

/**
 * Glowing status pill — used in headers, dashboards, and workflow cards.
 */
export function LiveProcessBadge({
  tone = "running",
  label,
  ariaLabel,
  className,
  variant = "pill",
}: LiveProcessBadgeProps) {
  if (variant === "dot") {
    const accessibleName = ariaLabel ?? label;
    return (
      <span
        className={cn("inline-flex items-center justify-center", className)}
        role="status"
        aria-label={accessibleName}
        title={accessibleName}
      >
        <span
          className={cn("h-2.5 w-2.5 shrink-0 rounded-full", dotOnlyStyles[tone])}
          aria-hidden="true"
        />
      </span>
    );
  }

  return (
    <span
      className={cn(
        "inline-flex items-center gap-2.5 rounded-full px-3.5 py-1.5 text-sm font-medium",
        pillStyles[tone],
        className,
      )}
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", dotStyles[tone])}
        aria-hidden="true"
      />
      {label ? <span>{label}</span> : null}
    </span>
  );
}

/**
 * Maps API session status to badge/dot tone (color).
 *
 * - running → blue (in progress)
 * - paused_* → orange (needs user input)
 * - completed → green
 * - failed_* / auto_archived → red
 * - created and other idle → gray
 */
export function sessionStatusToTone(status: string): LiveProcessTone {
  if (status === "running") return "running";
  if (status === "completed") return "complete";
  if (status.startsWith("failed_") || status === "auto_archived") return "failed";
  if (status.startsWith("paused_")) return "attention";
  if (status === "created") return "saved";
  return "saved";
}

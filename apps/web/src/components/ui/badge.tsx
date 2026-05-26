import * as React from "react";

import { cn } from "@/lib/utils";

const VARIANT = {
  neutral: "bg-slate-100 text-slate-700",
  success: "bg-emerald-100 text-emerald-800",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-red-100 text-red-800",
  info: "bg-blue-100 text-blue-800",
} as const;

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  readonly variant?: keyof typeof VARIANT;
}

export function Badge({ className, variant = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        VARIANT[variant],
        className,
      )}
      {...props}
    />
  );
}

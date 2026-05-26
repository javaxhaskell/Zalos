"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

const VARIANT = {
  primary:
    "bg-slate-900 text-white hover:bg-slate-800 active:bg-slate-700 disabled:bg-slate-300",
  secondary:
    "bg-white text-slate-900 border border-slate-300 hover:border-slate-400 active:bg-slate-50 disabled:text-slate-400",
  destructive:
    "bg-red-600 text-white hover:bg-red-700 active:bg-red-800 disabled:bg-red-300",
  success:
    "bg-emerald-600 text-white hover:bg-emerald-700 active:bg-emerald-800 disabled:bg-emerald-300",
  ghost:
    "bg-transparent text-slate-700 hover:bg-slate-100 disabled:text-slate-400",
} as const;

const SIZE = {
  sm: "h-8 px-3 text-sm",
  md: "h-10 px-4 text-sm",
  lg: "h-12 px-6 text-base",
} as const;

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: keyof typeof VARIANT;
  readonly size?: keyof typeof SIZE;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
        "disabled:cursor-not-allowed",
        VARIANT[variant],
        SIZE[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";

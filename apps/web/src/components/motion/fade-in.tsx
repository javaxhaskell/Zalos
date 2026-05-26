"use client";

import { useEffect, useState, type ReactNode } from "react";

import { usePrefersReducedMotion, staggerDelay } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

export interface FadeInProps {
  readonly children: ReactNode;
  readonly className?: string;
  /** Delay before the entrance animation starts (ms). */
  readonly delayMs?: number;
}

/**
 * Subtle entrance motion. Content stays visible at all times — we never
 * mount with opacity 0, which caused blank pages after SSR/hydration.
 */
export function FadeIn({ children, className, delayMs = 0 }: FadeInProps) {
  const reduced = usePrefersReducedMotion();
  const [animate, setAnimate] = useState(reduced);

  useEffect(() => {
    if (reduced) return;
    const timer = window.setTimeout(() => setAnimate(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [reduced, delayMs]);

  if (reduced) {
    return <div className={className}>{children}</div>;
  }

  return (
    <div
      className={cn(
        "opacity-100 transition-[transform,opacity] duration-200 ease-out",
        animate ? "translate-y-0" : "translate-y-1",
        className,
      )}
    >
      {children}
    </div>
  );
}

export interface AnimatedSectionProps {
  readonly children: ReactNode;
  readonly className?: string;
  readonly index?: number;
}

export function AnimatedSection({
  children,
  className,
  index = 0,
}: AnimatedSectionProps) {
  return (
    <FadeIn delayMs={staggerDelay(index)} className={className}>
      {children}
    </FadeIn>
  );
}

export interface PageShellProps {
  readonly children: ReactNode;
  readonly className?: string;
}

export function PageShell({ children, className }: PageShellProps) {
  return <div className={className}>{children}</div>;
}

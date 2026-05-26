"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { OPEN_TECHNICAL_DETAILS_EVENT } from "@/lib/technical-details";
import { cn } from "@/lib/utils";

export interface SmoothCollapseProps {
  readonly title: string;
  readonly description?: string;
  readonly defaultOpen?: boolean;
  readonly children: ReactNode;
  readonly className?: string;
  /** When set, listens for ``openTechnicalDetails`` and expands this block. */
  readonly sectionId?: string;
}

/**
 * Collapsible block with height/opacity transition and chevron rotation.
 */
export function SmoothCollapse({
  title,
  description,
  defaultOpen = false,
  children,
  className,
  sectionId,
}: SmoothCollapseProps) {
  const [open, setOpen] = useState(defaultOpen);
  const reduced = usePrefersReducedMotion();

  useEffect(() => {
    if (!sectionId) return;
    function onOpenRequest(event: Event) {
      const detail = (event as CustomEvent<{ sectionId?: string }>).detail;
      if (detail?.sectionId === sectionId) {
        setOpen(true);
      }
    }
    window.addEventListener(OPEN_TECHNICAL_DETAILS_EVENT, onOpenRequest);
    return () => {
      window.removeEventListener(OPEN_TECHNICAL_DETAILS_EVENT, onOpenRequest);
    };
  }, [sectionId]);

  return (
    <section
      className={cn(
        "rounded-lg border border-slate-200 bg-white shadow-sm",
        className,
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-slate-50/80"
      >
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-xs text-slate-600">{description}</p>
          ) : null}
        </div>
        <ChevronDown
          aria-hidden
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0 text-slate-500",
            reduced ? "" : "transition-transform duration-200 ease-out",
            open && "rotate-180",
          )}
        />
      </button>
      {reduced ? (
        open ? (
          <div className="border-t border-slate-200 px-4 py-4">{children}</div>
        ) : null
      ) : (
        <div
          className={cn(
            "grid border-t border-slate-200 transition-[grid-template-rows,opacity] duration-200 ease-out",
            open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
          )}
        >
          <div className="overflow-hidden">
            <div className="px-4 py-4">{children}</div>
          </div>
        </div>
      )}
    </section>
  );
}

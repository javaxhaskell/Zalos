"use client";

import { useCallback, useState } from "react";
import { motion } from "framer-motion";

import { usePrefersReducedMotion } from "@/hooks/use-prefers-reduced-motion";
import { cn } from "@/lib/utils";

import {
  AUTHOR_WALKTHROUGH_STEPS,
  REPAIR_WALKTHROUGH_STEPS,
  type WalkthroughStep,
  type WalkthroughStepId,
  type WalkthroughVariant,
} from "./constants";
import { MockupFrame } from "./mockup-frame";

export interface FeatureWalkthroughProps {
  readonly className?: string;
  readonly title?: string;
  readonly subtitle?: string;
  readonly variant?: WalkthroughVariant;
  readonly steps?: readonly WalkthroughStep[];
  readonly initialStepId?: WalkthroughStepId;
  readonly headingId?: string;
}

function slugifyHeadingId(title: string): string {
  return title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

interface StepNavButtonProps {
  readonly step: WalkthroughStep;
  readonly isActive: boolean;
  readonly onSelect: (id: WalkthroughStepId) => void;
  readonly layout: "vertical" | "horizontal";
}

function StepNavButton({
  step,
  isActive,
  onSelect,
  layout,
}: StepNavButtonProps) {
  const indexLabel = String(step.index).padStart(2, "0");

  return (
    <motion.button
      type="button"
      onClick={(event) => {
        event.preventDefault();
        onSelect(step.id);
      }}
      aria-current={isActive ? "step" : undefined}
      className={cn(
        "group relative shrink-0 text-left transition-colors",
        layout === "vertical"
          ? "min-h-[4rem] w-full rounded-md px-5 py-4"
          : "min-h-[3.25rem] min-w-[12rem] max-w-[15rem] snap-start rounded-lg px-4 py-2.5 sm:min-w-[13rem]",
        isActive
          ? layout === "vertical"
            ? "bg-slate-100/70"
            : "bg-white shadow-sm ring-1 ring-slate-200"
          : "bg-transparent hover:bg-slate-50/80",
      )}
      whileTap={{ scale: 0.995 }}
    >
      <span
        className={cn(
          "absolute left-0 rounded-full bg-slate-900 transition-opacity duration-300",
          layout === "vertical"
            ? "bottom-3 top-3 w-[3px]"
            : "bottom-2.5 top-2.5 w-0.5",
          isActive ? "opacity-100" : "opacity-0",
        )}
        aria-hidden
      />

      <div
        className={cn(
          "flex items-center",
          layout === "vertical" ? "gap-3" : "gap-2.5",
        )}
      >
        <span
          className={cn(
            "font-mono tabular-nums leading-none transition-colors duration-300",
            layout === "vertical"
              ? "text-sm font-medium"
              : "text-xs font-medium",
            isActive ? "text-slate-900" : "text-slate-400/90",
          )}
        >
          {indexLabel}
        </span>
        <p
          className={cn(
            "min-w-0 flex-1 font-semibold tracking-tight transition-colors duration-300",
            layout === "vertical" ? "text-lg leading-snug" : "text-sm",
            isActive ? "text-slate-950" : "text-slate-600/80",
          )}
        >
          {step.title}
        </p>
      </div>
    </motion.button>
  );
}

/**
 * Interactive walkthrough for Author or Repair workflows with a product mockup.
 */
export function FeatureWalkthrough({
  className,
  title = "Author workflow",
  subtitle,
  variant = "author",
  steps = variant === "repair"
    ? REPAIR_WALKTHROUGH_STEPS
    : AUTHOR_WALKTHROUGH_STEPS,
  initialStepId = "start",
  headingId,
}: FeatureWalkthroughProps) {
  const reducedMotion = usePrefersReducedMotion();
  const [activeStepId, setActiveStepId] =
    useState<WalkthroughStepId>(initialStepId);
  const resolvedHeadingId =
    headingId ?? `${slugifyHeadingId(title)}-walkthrough-heading`;
  const stepsAriaLabel = `${title} steps`;
  const mockupNavAriaLabel = `${title} mockup navigation`;

  const handleSelect = useCallback((id: WalkthroughStepId) => {
    setActiveStepId(id);
  }, []);

  const handleAdvance = useCallback(() => {
    const currentIndex = steps.findIndex((step) => step.id === activeStepId);
    const nextStep = steps[currentIndex + 1];

    if (nextStep) {
      handleSelect(nextStep.id);
    }
  }, [activeStepId, handleSelect, steps]);

  return (
    <section
      className={cn("w-full", className)}
      aria-labelledby={resolvedHeadingId}
    >
      <header className="mb-8 max-w-2xl">
        <h2
          id={resolvedHeadingId}
          className="text-xl font-semibold tracking-tight text-slate-950 sm:text-2xl"
        >
          {title}
        </h2>
        {subtitle ? (
          <p className="mt-2 text-sm leading-relaxed text-slate-600">{subtitle}</p>
        ) : null}
      </header>

      {/* Desktop: static two-column layout. */}
      <div className="hidden lg:grid lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:gap-10 xl:gap-14">
        <div role="tablist" aria-label={stepsAriaLabel}>
          <div className="flex w-full flex-col gap-3">
            {steps.map((step) => (
              <StepNavButton
                key={step.id}
                step={step}
                isActive={activeStepId === step.id}
                onSelect={handleSelect}
                layout="vertical"
              />
            ))}
          </div>
        </div>

        <motion.div
          initial={reducedMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        >
          <MockupFrame
            activeStepId={activeStepId}
            steps={steps}
            variant={variant}
            navAriaLabel={mockupNavAriaLabel}
            onAdvance={handleAdvance}
            onSelect={handleSelect}
          />
        </motion.div>
      </div>

      {/* Mobile: horizontal step strip + mockup. */}
      <div className="grid gap-8 lg:hidden">
        <div role="tablist" aria-label={stepsAriaLabel}>
          <div className="-mx-1 flex snap-x snap-mandatory gap-2.5 overflow-x-auto px-1 pb-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {steps.map((step) => (
              <StepNavButton
                key={step.id}
                step={step}
                isActive={activeStepId === step.id}
                onSelect={handleSelect}
                layout="horizontal"
              />
            ))}
          </div>
        </div>

        <motion.div
          initial={reducedMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
        >
          <MockupFrame
            activeStepId={activeStepId}
            steps={steps}
            variant={variant}
            navAriaLabel={mockupNavAriaLabel}
            onAdvance={handleAdvance}
            onSelect={handleSelect}
          />
        </motion.div>
      </div>
    </section>
  );
}

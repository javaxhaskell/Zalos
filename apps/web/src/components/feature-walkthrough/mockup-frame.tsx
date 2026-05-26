"use client";

import { AnimatePresence, motion } from "framer-motion";

import { cn } from "@/lib/utils";

import {
  type WalkthroughStep,
  type WalkthroughStepId,
  type WalkthroughVariant,
} from "./constants";
import { MockupPanel } from "./mockup-panels";

export interface MockupFrameProps {
  readonly activeStepId: WalkthroughStepId;
  readonly steps: readonly WalkthroughStep[];
  readonly variant?: WalkthroughVariant;
  readonly navAriaLabel?: string;
  readonly onAdvance?: () => void;
  readonly onSelect?: (id: WalkthroughStepId) => void;
  readonly className?: string;
}

export function MockupFrame({
  activeStepId,
  steps,
  variant = "author",
  navAriaLabel = "Workflow mockup navigation",
  onAdvance,
  onSelect,
  className,
}: MockupFrameProps) {
  const activeIndex = steps.findIndex((step) => step.id === activeStepId);

  return (
    <div
      className={cn(
        "w-full overflow-hidden rounded-xl border border-slate-200/90 bg-white text-left shadow-[0_8px_30px_rgb(15_23_42_/_0.06)]",
        className,
      )}
    >
      <div className="flex items-center gap-1 border-b border-slate-100 bg-slate-50/90 px-3 py-2">
        <span className="mr-2 size-2 rounded-full bg-[#FF5F57]" />
        <span className="mr-1 size-2 rounded-full bg-[#FFBD2E]" />
        <span className="size-2 rounded-full bg-[#28CA42]" />
        <div
          className="ml-auto flex gap-0.5"
          role="tablist"
          aria-label={navAriaLabel}
        >
          {steps.map((step, index) => {
            const isCurrent = step.id === activeStepId;
            const isComplete = index < activeIndex;

            return (
              <button
                key={step.id}
                type="button"
                role="tab"
                aria-selected={isCurrent}
                aria-current={isCurrent ? "step" : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  onSelect?.(step.id);
                }}
                className={cn(
                  "relative flex h-5 min-w-[3.25rem] items-center justify-center overflow-hidden rounded px-1.5 transition-colors",
                  onSelect ? "cursor-pointer hover:opacity-90" : "cursor-default",
                )}
              >
                <motion.div
                  className="absolute inset-0 rounded bg-slate-900"
                  initial={false}
                  animate={{
                    opacity: isCurrent ? 1 : isComplete ? 0.12 : 0,
                    scaleX: isCurrent || isComplete ? 1 : 0,
                  }}
                  transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                  style={{ originX: 0 }}
                />
                <span
                  className={cn(
                    "relative z-10 text-[9px] font-medium transition-colors duration-300",
                    isCurrent
                      ? "text-white"
                      : isComplete
                        ? "text-slate-700"
                        : "text-slate-400",
                  )}
                >
                  {step.navLabel}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="relative h-[min(22rem,49vw)] sm:h-[22rem]">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={activeStepId}
            className="absolute inset-0 min-h-0 overflow-hidden"
            initial={{ opacity: 0, filter: "blur(6px)", y: 8 }}
            animate={{ opacity: 1, filter: "blur(0px)", y: 0 }}
            exit={{ opacity: 0, filter: "blur(4px)", y: -6 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
          >
            <MockupPanel
              stepId={activeStepId}
              variant={variant}
              isActive
              onAdvance={onAdvance}
            />
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  );
}

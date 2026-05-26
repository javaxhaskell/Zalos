"use client";

import { useCallback, useState } from "react";
import { ArrowRight } from "lucide-react";
import { motion } from "framer-motion";

import { AnimatedPlanList } from "../animated-plan-list";
import {
  REPAIR_DEMO_DESCRIPTION,
  REPAIR_DEMO_PLAN_ITEMS,
} from "../constants";

export interface RepairDescribePanelProps {
  readonly isActive: boolean;
  readonly onAdvance?: () => void;
}

function stopInteractionScroll(event: React.SyntheticEvent) {
  event.stopPropagation();
}

export function RepairDescribePanel({
  isActive,
  onAdvance,
}: RepairDescribePanelProps) {
  const [description, setDescription] = useState(REPAIR_DEMO_DESCRIPTION);
  const [planProgressPercent, setPlanProgressPercent] = useState(28);

  const handlePlanProgressChange = useCallback((percent: number) => {
    setPlanProgressPercent(percent);
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-50/80 p-3">
      <div className="mb-2 shrink-0">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Repair session
        </p>
        <p className="text-xs font-medium text-slate-900">
          Describe the problem
        </p>
      </div>

      <label
        className="block shrink-0"
        onMouseDown={stopInteractionScroll}
      >
        <span className="text-[10px] font-medium text-slate-600">
          Problem description
        </span>
        <motion.div
          className="mt-0.5 rounded-md border border-slate-200 bg-white/90 p-1.5 backdrop-blur-sm"
          animate={
            isActive
              ? {
                  borderColor: "rgb(148 163 184)",
                  boxShadow: "0 1px 2px rgb(15 23 42 / 0.04)",
                }
              : { borderColor: "rgb(226 232 240)", boxShadow: "none" }
          }
        >
          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            onMouseDown={stopInteractionScroll}
            onClick={stopInteractionScroll}
            onKeyDown={stopInteractionScroll}
            onWheel={stopInteractionScroll}
            rows={3}
            spellCheck={false}
            aria-label="Problem description"
            className="block max-h-[3.25rem] w-full resize-none overflow-y-auto border-0 bg-transparent p-0 text-[10px] leading-snug text-slate-700 placeholder:text-slate-400 focus:outline-none focus:ring-0"
          />
        </motion.div>
      </label>

      <div className="mt-2 flex min-h-0 flex-1 flex-col">
        <div className="mb-1 flex shrink-0 items-center justify-between">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            Understood plan
          </p>
          <motion.span
            className="rounded-full bg-blue-50 px-1.5 py-0.5 text-[9px] font-medium text-blue-700"
            animate={
              isActive ? { opacity: 1, scale: 1 } : { opacity: 0.5, scale: 0.95 }
            }
          >
            Draft
          </motion.span>
        </div>

        <AnimatedPlanList
          items={REPAIR_DEMO_PLAN_ITEMS}
          isActive={isActive}
          idleProgressPercent={28}
          completeProgressPercent={72}
          onProgressChange={handlePlanProgressChange}
        />

        <div className="mt-2 shrink-0 space-y-1.5 border-t border-slate-100/80 pt-2">
          <div className="flex items-center gap-1.5">
            <motion.div
              className="h-0.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-200"
              aria-hidden
            >
              <motion.div
                className="h-full rounded-full bg-slate-400"
                initial={false}
                animate={{ width: `${planProgressPercent}%` }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
              />
            </motion.div>
            <p className="shrink-0 text-[9px] leading-none text-slate-400">
              Planning repair…
            </p>
          </div>

          <motion.button
            type="button"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              onAdvance?.();
            }}
            onMouseDown={stopInteractionScroll}
            className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-slate-900 px-2.5 py-1 text-[10px] font-medium text-white transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/20 focus-visible:ring-offset-2"
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
          >
            Continue
            <ArrowRight className="size-3" aria-hidden />
          </motion.button>
        </div>
      </div>
    </div>
  );
}

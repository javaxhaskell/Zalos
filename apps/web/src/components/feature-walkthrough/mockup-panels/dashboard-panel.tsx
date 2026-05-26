"use client";

import { ArrowRight, Sparkles } from "lucide-react";
import { motion } from "framer-motion";

import { cn } from "@/lib/utils";

export interface DashboardPanelProps {
  readonly isActive: boolean;
  readonly onAdvance?: () => void;
}

export function DashboardPanel({ isActive, onAdvance }: DashboardPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-slate-50/80 p-3">
      <div className="mb-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          AgentForge
        </p>
        <p className="text-xs font-medium text-slate-900">Dashboard</p>
      </div>

      <div className="grid flex-1 gap-1.5">
        <motion.div
          className={cn(
            "rounded-lg border bg-white p-2.5 shadow-sm transition-shadow",
            isActive
              ? "border-slate-900/15 shadow-md ring-1 ring-slate-900/5"
              : "border-slate-200",
          )}
          animate={
            isActive
              ? { scale: 1, opacity: 1 }
              : { scale: 0.98, opacity: 0.72 }
          }
          transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-xs font-semibold text-slate-900">
                Author a new agent
              </p>
              <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
                Upload CSV/XLSX and generate a runnable agent with validation
                evidence.
              </p>
            </div>
            <Sparkles
              className="size-3.5 shrink-0 text-slate-400"
              aria-hidden
            />
          </div>
          <div className="mt-2 rounded border border-emerald-200/80 bg-emerald-50/60 px-2 py-1.5">
            <p className="text-[10px] font-medium text-slate-800">
              Expense Exception Review
            </p>
          </div>
          <motion.button
            type="button"
            onClick={(event) => {
              event.preventDefault();
              onAdvance?.();
            }}
            className="mt-2 inline-flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-md bg-slate-900 px-2.5 py-1.5 text-[10px] font-medium text-white transition-colors hover:bg-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/20 focus-visible:ring-offset-2"
            animate={
              isActive
                ? { boxShadow: "0 0 0 2px rgb(15 23 42 / 0.12)" }
                : { boxShadow: "0 0 0 0px rgb(15 23 42 / 0)" }
            }
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.99 }}
          >
            Start authoring
            <ArrowRight className="size-3" aria-hidden />
          </motion.button>
        </motion.div>

        <div className="rounded-lg border border-slate-200 bg-white/60 p-2.5 opacity-60">
          <p className="text-xs font-semibold text-slate-700">
            Repair an existing agent
          </p>
          <p className="mt-1 text-[10px] text-slate-500">
            Invoice Aging Boundary Repair sample
          </p>
          <div className="mt-2 h-6 rounded-md border border-slate-200 bg-slate-50" />
        </div>
      </div>
    </div>
  );
}

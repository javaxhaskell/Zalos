"use client";

import { Check, FileSpreadsheet, Upload } from "lucide-react";
import { motion } from "framer-motion";

import { EXPENSE_DEMO_FILE } from "../constants";

export interface UploadPanelProps {
  readonly isActive: boolean;
  readonly onAdvance?: () => void;
}

export function UploadPanel({ isActive, onAdvance }: UploadPanelProps) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-slate-50/80 p-2.5">
      <div className="mb-1.5">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Author session
        </p>
        <p className="text-xs font-medium text-slate-900">Upload sample file</p>
      </div>

      <motion.button
        type="button"
        onClick={(event) => {
          event.preventDefault();
          onAdvance?.();
        }}
        className="cursor-pointer rounded-lg border-2 border-dashed border-slate-300 bg-white/70 px-3 py-2.5 text-center backdrop-blur-sm transition-colors hover:border-slate-400 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/20 focus-visible:ring-offset-2"
        animate={
          isActive
            ? { borderColor: "rgb(100 116 139)", backgroundColor: "rgb(248 250 252 / 0.95)" }
            : { borderColor: "rgb(203 213 225)", backgroundColor: "rgb(255 255 255 / 0.7)" }
        }
        whileHover={isActive ? { scale: 1.005 } : undefined}
        whileTap={isActive ? { scale: 0.995 } : undefined}
        transition={{ duration: 0.3 }}
      >
        <Upload className="mx-auto size-4 text-slate-400" aria-hidden />
        <p className="mt-1.5 text-[10px] font-medium text-slate-700">
          Drop CSV or XLSX, or click to choose
        </p>
        <p className="mt-0.5 text-[9px] text-slate-500">Up to 25 MB</p>
      </motion.button>

      <motion.div
        className="mt-1.5 rounded-lg border border-emerald-200 bg-emerald-50/80 px-2.5 py-1.5"
        initial={false}
        animate={
          isActive
            ? { opacity: 1, y: 0, filter: "blur(0px)" }
            : { opacity: 0.5, y: 4, filter: "blur(1px)" }
        }
        transition={{ duration: 0.35, delay: isActive ? 0.1 : 0 }}
      >
        <div className="flex items-center gap-2">
          <FileSpreadsheet
            className="size-3.5 shrink-0 text-emerald-700"
            aria-hidden
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-[10px] font-medium text-emerald-900">
              {EXPENSE_DEMO_FILE.filename}
            </p>
            <p className="text-[9px] text-emerald-800">
              {EXPENSE_DEMO_FILE.rowCount} rows · {EXPENSE_DEMO_FILE.sizeLabel}
            </p>
          </div>
          <Check className="size-3 text-emerald-600" aria-hidden />
        </div>
      </motion.div>

      <div className="mt-2 shrink-0">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
            File profile
          </p>
          <p className="shrink-0 text-[8px] text-slate-400">
            {EXPENSE_DEMO_FILE.columns.length} columns · {EXPENSE_DEMO_FILE.rowCount}{" "}
            rows
          </p>
        </div>

        <div className="max-h-[10rem] overflow-y-auto rounded-md border border-slate-200 bg-white">
          <table className="w-full text-left text-[9px]">
            <thead className="sticky top-0 z-10">
              <tr className="border-b border-slate-100 bg-slate-50/95 text-slate-500 backdrop-blur-sm">
                <th className="px-2 py-0.5 font-medium">Column</th>
                <th className="px-2 py-0.5 font-medium">Type</th>
                <th className="px-2 py-0.5 font-medium">Nulls</th>
              </tr>
            </thead>
            <tbody>
              {EXPENSE_DEMO_FILE.columns.map((col, i) => (
                <motion.tr
                  key={col.name}
                  className="border-b border-slate-50 last:border-0"
                  initial={false}
                  animate={
                    isActive
                      ? { opacity: 1, x: 0 }
                      : { opacity: 0.4, x: -4 }
                  }
                  transition={{ duration: 0.25, delay: isActive ? i * 0.05 : 0 }}
                >
                  <td className="px-2 py-0.5 font-mono text-slate-800">
                    {col.name}
                  </td>
                  <td className="px-2 py-0.5 text-slate-600">{col.type}</td>
                  <td className="px-2 py-0.5 tabular-nums text-slate-600">
                    {col.nulls}
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

"use client";

import { useEffect, useState, type MouseEvent } from "react";
import {
  Check,
  Download,
  FileArchive,
  FileCheck,
  FileText,
  X,
} from "lucide-react";
import { motion } from "framer-motion";

export interface RepairReviewPanelProps {
  readonly isActive: boolean;
  readonly onAdvance?: () => void;
}

const VALIDATION_CHECKS = [
  { label: "Failure reproduced on sample data", status: "pass" },
  { label: "Root cause identified", status: "pass" },
  { label: "Fix applied and re-tested", status: "pass" },
  { label: "Aging buckets verified after fix", status: "pass" },
];

const DOWNLOADS = [
  {
    icon: FileText,
    label: "Repair report",
    detail: "What failed, why, and what changed",
  },
  {
    icon: FileCheck,
    label: "Updated agent files",
    detail: "Repaired agent package",
  },
  {
    icon: FileArchive,
    label: "Audit package",
    detail: "Complete audit record",
  },
] as const;

const DOWNLOAD_FEEDBACK_MS = 1400;

type ReviewDecision = "idle" | "approved" | "declined";

export function RepairReviewPanel({ isActive }: RepairReviewPanelProps) {
  const [decision, setDecision] = useState<ReviewDecision>("idle");
  const [downloadingLabel, setDownloadingLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!isActive) {
      setDecision("idle");
      setDownloadingLabel(null);
    }
  }, [isActive]);

  useEffect(() => {
    if (!downloadingLabel) return;
    const timer = window.setTimeout(
      () => setDownloadingLabel(null),
      DOWNLOAD_FEEDBACK_MS,
    );
    return () => window.clearTimeout(timer);
  }, [downloadingLabel]);

  const handleDownloadClick = (
    event: MouseEvent<HTMLButtonElement>,
    label: string,
  ) => {
    event.preventDefault();
    event.stopPropagation();
    setDownloadingLabel(label);
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-slate-50/80 p-3">
      <div className="mb-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Repair review
        </p>
        <p className="text-xs font-medium text-slate-900">
          Review, approve & download
        </p>
      </div>

      <motion.div
        className="rounded-lg border border-slate-200/90 bg-white px-2.5 py-2"
        animate={isActive ? { opacity: 1, y: 0 } : { opacity: 0.55, y: 4 }}
      >
        <p className="text-[10px] font-medium text-slate-900">
          Failure reproduced
        </p>
        <p className="mt-0.5 text-[9px] text-slate-600">
          Invoices exactly 31 days overdue were assigned to the 1-30 day bucket
          instead of 31-60 days.
        </p>
      </motion.div>

      <motion.div
        className="mt-2 rounded-lg border border-amber-200/80 bg-amber-50/70 px-2.5 py-2"
        animate={isActive ? { opacity: 1, y: 0 } : { opacity: 0.55, y: 4 }}
      >
        <p className="text-[10px] font-medium text-amber-950">
          Fix proposed: correct 31-day boundary rule
        </p>
        <p className="mt-0.5 text-[9px] text-amber-900/80">
          Review the proposed change and approve before applying the fix.
        </p>
        <div className="mt-2 flex gap-1.5">
          <motion.button
            type="button"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setDecision("approved");
            }}
            aria-pressed={decision === "approved"}
            className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-emerald-600 px-2 py-0.5 text-[9px] font-medium text-white transition-colors hover:bg-emerald-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600/30 focus-visible:ring-offset-2"
            whileHover={decision === "approved" ? undefined : { scale: 1.03 }}
            whileTap={decision === "approved" ? undefined : { scale: 0.97 }}
          >
            {decision === "approved" ? (
              <>
                <Check className="size-2.5" aria-hidden />
                Approved
              </>
            ) : (
              "Approve"
            )}
          </motion.button>
          <motion.button
            type="button"
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              setDecision("declined");
            }}
            aria-pressed={decision === "declined"}
            className="inline-flex cursor-pointer items-center gap-1 rounded-md bg-red-600 px-2 py-0.5 text-[9px] font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-600/30 focus-visible:ring-offset-2"
            whileHover={decision === "declined" ? undefined : { scale: 1.03 }}
            whileTap={decision === "declined" ? undefined : { scale: 0.97 }}
          >
            {decision === "declined" ? (
              <>
                <X className="size-2.5" aria-hidden />
                Declined
              </>
            ) : (
              "Decline"
            )}
          </motion.button>
        </div>
      </motion.div>

      <div className="mt-2">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          Validation
        </p>
        <ul className="space-y-0.5">
          {VALIDATION_CHECKS.map((check, i) => (
            <motion.li
              key={check.label}
              className="flex items-center justify-between rounded border border-slate-200/80 bg-white px-2 py-0.5"
              initial={false}
              animate={
                isActive ? { opacity: 1, x: 0 } : { opacity: 0.4, x: -4 }
              }
              transition={{ duration: 0.25, delay: isActive ? i * 0.05 : 0 }}
            >
              <span className="text-[10px] text-slate-700">{check.label}</span>
              <motion.span
                className="rounded-full bg-emerald-50 px-1.5 py-0.5 text-[9px] font-medium text-emerald-700"
                animate={isActive ? { scale: [1, 1.06, 1] } : { scale: 1 }}
                transition={
                  isActive
                    ? { duration: 0.35, delay: 0.2 + i * 0.08 }
                    : { duration: 0 }
                }
              >
                Pass
              </motion.span>
            </motion.li>
          ))}
        </ul>
      </div>

      <div className="mt-2">
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
          Downloads ready
        </p>
        <ul className="space-y-1">
          {DOWNLOADS.map((item, i) => {
            const isDownloading = downloadingLabel === item.label;

            return (
              <motion.li
                key={item.label}
                initial={false}
                animate={
                  isActive
                    ? { opacity: 1, y: 0, filter: "blur(0px)" }
                    : { opacity: 0.45, y: 6, filter: "blur(1px)" }
                }
                transition={{
                  duration: 0.35,
                  delay: isActive ? 0.15 + i * 0.07 : 0,
                }}
              >
                <button
                  type="button"
                  onClick={(event) => handleDownloadClick(event, item.label)}
                  aria-busy={isDownloading}
                  className={`flex w-full cursor-pointer items-center gap-2 rounded-md border px-2 py-1 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900/20 focus-visible:ring-offset-2 ${
                    isDownloading
                      ? "border-slate-900/20 bg-slate-100 ring-1 ring-slate-900/10"
                      : "border-slate-200 bg-white/90 hover:border-slate-300 hover:bg-white active:bg-slate-50"
                  }`}
                >
                  <item.icon
                    className={`size-3.5 shrink-0 transition-colors ${
                      isDownloading ? "text-slate-700" : "text-slate-500"
                    }`}
                    aria-hidden
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-[10px] font-medium text-slate-800">
                      {item.label}
                    </p>
                    <p
                      className={`text-[9px] transition-colors ${
                        isDownloading
                          ? "font-medium text-slate-700"
                          : "text-slate-500"
                      }`}
                    >
                      {isDownloading ? "Downloading…" : item.detail}
                    </p>
                  </div>
                  <Download
                    className={`size-3 shrink-0 transition-colors ${
                      isDownloading ? "text-slate-700" : "text-slate-400"
                    }`}
                    aria-hidden
                  />
                </button>
              </motion.li>
            );
          })}
        </ul>
      </div>

      <motion.div
        className="mt-1.5 flex items-center gap-2 rounded-md bg-emerald-50 px-2 py-1"
        animate={isActive ? { opacity: 1 } : { opacity: 0.5 }}
      >
        <span className="size-1.5 rounded-full bg-emerald-500" aria-hidden />
        <p className="text-[9px] font-medium text-emerald-800">
          Repair complete. All files ready to download.
        </p>
      </motion.div>
    </div>
  );
}

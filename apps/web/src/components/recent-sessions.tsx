"use client";

import { ChevronLeft, ChevronRight, Download, Search } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { LiveProcessBadge, sessionStatusToTone } from "@/components/live-process-badge";
import { SkeletonTableRows } from "@/components/motion";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/components/ui/table";
import {
  archiveSession,
  deleteSession,
  getEvents,
  listSessions,
  restoreSession,
  type SessionListItem,
  type SessionListView,
  type WorkspaceEvent,
} from "@/lib/api-client";
import { deriveWorkItem } from "@/lib/session-work-item";
import { cn } from "@/lib/utils";
import { deriveWorkflowStatusLabel } from "@/lib/workflow-timeline";

interface SessionRow extends SessionListItem {
  readonly workItem: string;
}

const SESSIONS_FETCH_LIMIT = 500;
const PAGE_SIZE = 8;

const SESSION_VIEWS: ReadonlyArray<{
  readonly id: SessionListView;
  readonly label: string;
}> = [
  { id: "active", label: "Active" },
  { id: "archived", label: "Archived" },
  { id: "deleted", label: "Recently deleted" },
];

const downloadClassName = cn(
  "inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-md border border-slate-300 px-3 text-xs font-medium",
  "bg-white text-slate-900 hover:bg-slate-50",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-white",
);

const actionButtonClassName = cn(
  "inline-flex h-8 items-center justify-center rounded-md border px-3 text-xs font-medium",
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
  "disabled:cursor-not-allowed disabled:opacity-50",
);

const checkboxClassName = cn(
  "h-4 w-4 rounded border-slate-300 text-slate-900",
  "focus:ring-2 focus:ring-slate-500 focus:ring-offset-1",
  "disabled:cursor-not-allowed disabled:opacity-40",
);

function isRunningSession(status: string): boolean {
  return status === "running";
}

function liveTone(status: string) {
  return sessionStatusToTone(status);
}

function workflowTypeLabel(workflow: string): string {
  return workflow === "author" ? "Author" : "Repair";
}

function matchesSessionSearch(session: SessionRow, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;

  const statusLabel = deriveWorkflowStatusLabel(session).toLowerCase();
  const haystack = [
    session.workItem,
    workflowTypeLabel(session.workflow),
    session.workflow,
    statusLabel,
    session.status,
    formatTime(session.started_at),
    session.started_at,
    session.id,
  ]
    .join(" ")
    .toLowerCase();

  return haystack.includes(trimmed);
}

function SessionViewTabs({
  view,
  onViewChange,
  disabled = false,
}: {
  readonly view: SessionListView;
  readonly onViewChange: (view: SessionListView) => void;
  readonly disabled?: boolean;
}) {
  return (
    <div
      role="tablist"
      aria-label="Workflow list view"
      className="inline-flex rounded-md border border-slate-200 bg-slate-50 p-0.5"
    >
      {SESSION_VIEWS.map((tab) => {
        const selected = view === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={selected}
            disabled={disabled}
            onClick={() => onViewChange(tab.id)}
            className={cn(
              "rounded px-3 py-1 text-xs font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2",
              "disabled:cursor-not-allowed disabled:opacity-50",
              selected
                ? "bg-white text-slate-900 shadow-sm"
                : "text-slate-600 hover:text-slate-900",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

function RecentSessionsHeader({
  view,
  onViewChange,
  searchQuery,
  onSearchChange,
  onDownloadCleanList,
  downloadDisabled = false,
  disabled = false,
}: {
  readonly view: SessionListView;
  readonly onViewChange: (view: SessionListView) => void;
  readonly searchQuery: string;
  readonly onSearchChange: (value: string) => void;
  readonly onDownloadCleanList: () => void;
  readonly downloadDisabled?: boolean;
  readonly disabled?: boolean;
}) {
  return (
    <CardHeader className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle>Recent sessions</CardTitle>
        <SessionViewTabs view={view} onViewChange={onViewChange} disabled={disabled} />
      </div>
      <div className="flex w-full flex-col gap-2 sm:max-w-md sm:flex-row sm:items-center sm:ml-auto">
        <div className="relative w-full sm:min-w-0 sm:flex-1">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
          />
          <input
            type="search"
            value={searchQuery}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search workflows…"
            disabled={disabled}
            aria-label="Search recent workflows"
            className="w-full rounded-md border border-slate-300 py-1.5 pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-500 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500"
          />
        </div>
        <button
          type="button"
          onClick={onDownloadCleanList}
          disabled={disabled || downloadDisabled}
          aria-label="Download full list of sessions as CSV"
          className={downloadClassName}
        >
          <Download className="h-3.5 w-3.5" aria-hidden="true" />
          Download full list
        </button>
      </div>
    </CardHeader>
  );
}

interface SelectionFeedback {
  readonly variant: "success" | "error";
  readonly message: string;
}

function SessionSelectionBar({
  view,
  selectedCount,
  busy,
  onArchive,
  onRestore,
  onDelete,
  onClear,
}: {
  readonly view: SessionListView;
  readonly selectedCount: number;
  readonly busy: boolean;
  readonly onArchive?: () => void;
  readonly onRestore?: () => void;
  readonly onDelete: () => void;
  readonly onClear: () => void;
}) {
  return (
    <div
      role="toolbar"
      aria-label="Selected workflow actions"
      className="mb-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
    >
      <p className="text-sm text-slate-700">
        {selectedCount} workflow{selectedCount === 1 ? "" : "s"} selected
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onClear}
          disabled={busy}
          className={cn(actionButtonClassName, "border-slate-300 bg-white text-slate-700 hover:bg-slate-100")}
        >
          Clear selection
        </button>
        {view === "active" && onArchive ? (
          <button
            type="button"
            onClick={onArchive}
            disabled={busy}
            className={cn(actionButtonClassName, "border-slate-300 bg-white text-slate-800 hover:bg-slate-100")}
          >
            Archive selected
          </button>
        ) : null}
        {view !== "active" && onRestore ? (
          <button
            type="button"
            onClick={onRestore}
            disabled={busy}
            className={cn(actionButtonClassName, "border-slate-300 bg-white text-slate-800 hover:bg-slate-100")}
          >
            Restore selected
          </button>
        ) : null}
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          className={cn(actionButtonClassName, "border-red-200 bg-white text-red-800 hover:bg-red-50")}
        >
          {view === "active" ? "Delete selected" : "Delete permanently"}
        </button>
      </div>
    </div>
  );
}

function DeleteConfirmDialog({
  open,
  view,
  selectedCount,
  busy,
  onCancel,
  onConfirm,
}: {
  readonly open: boolean;
  readonly view: SessionListView;
  readonly selectedCount: number;
  readonly busy: boolean;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}) {
  const permanent = view !== "active";
  const title = permanent
    ? "Permanently delete selected workflows?"
    : "Delete selected workflows?";
  const description = permanent
    ? `This will permanently remove ${selectedCount} workflow${selectedCount === 1 ? "" : "s"} and their saved files from AgentForge. You cannot undo this action.`
    : `This moves ${selectedCount} workflow${selectedCount === 1 ? "" : "s"} to Recently deleted. You can restore them within 30 days, or delete them permanently from that tab.`;
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="delete-sessions-title"
      aria-describedby="delete-sessions-description"
      className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-0 shadow-lg backdrop:bg-slate-900/40"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      onClose={onCancel}
    >
      <div className="px-5 py-4">
        <h2 id="delete-sessions-title" className="text-base font-semibold text-slate-950">
          {title}
        </h2>
        <p id="delete-sessions-description" className="mt-2 text-sm leading-6 text-slate-600">
          {description}
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className={cn(actionButtonClassName, "border-slate-300 bg-white text-slate-700 hover:bg-slate-100")}
          >
            Keep workflows
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            className={cn(actionButtonClassName, "border-red-300 bg-red-700 text-white hover:bg-red-800")}
          >
            {permanent ? "Delete permanently" : "Move to Recently deleted"}
          </button>
        </div>
      </div>
    </dialog>
  );
}

function SessionPagination({
  currentPage,
  totalPages,
  onPageChange,
}: {
  readonly currentPage: number;
  readonly totalPages: number;
  readonly onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) return null;

  return (
    <nav
      aria-label="Recent sessions pages"
      className="mt-4 flex items-center justify-center gap-3"
    >
      <button
        type="button"
        onClick={() => onPageChange(currentPage - 1)}
        disabled={currentPage === 0}
        aria-label="Previous page"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
      >
        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
      </button>

      <div className="flex items-center gap-2" role="group" aria-label="Page indicators">
        {Array.from({ length: totalPages }, (_, pageIndex) => (
          <button
            key={pageIndex}
            type="button"
            onClick={() => onPageChange(pageIndex)}
            aria-label={`Page ${pageIndex + 1} of ${totalPages}`}
            aria-current={currentPage === pageIndex ? "page" : undefined}
            className={cn(
              "h-2 w-2 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400 focus:ring-offset-2",
              currentPage === pageIndex
                ? "bg-slate-800"
                : "bg-slate-300 hover:bg-slate-400",
            )}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={() => onPageChange(currentPage + 1)}
        disabled={currentPage >= totalPages - 1}
        aria-label="Next page"
        className="inline-flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
      >
        <ChevronRight className="h-4 w-4" aria-hidden="true" />
      </button>
    </nav>
  );
}

/**
 * Newest-first session list with human-readable work items for return-to-session links.
 */
export function RecentSessions({
  limit = SESSIONS_FETCH_LIMIT,
}: {
  readonly limit?: number;
}) {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [view, setView] = useState<SessionListView>("active");
  const [searchQuery, setSearchQuery] = useState("");
  const [currentPage, setCurrentPage] = useState(0);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [actionBusy, setActionBusy] = useState(false);
  const [feedback, setFeedback] = useState<SelectionFeedback | null>(null);
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);

  const loadSessions = useCallback(async () => {
    const rows = await listSessions(limit, view);
    const enriched = await Promise.all(
      rows.map(async (session) => {
        let events: WorkspaceEvent[] = [];
        try {
          const response = await getEvents(session.id);
          events = response.events;
        } catch {
          events = [];
        }
        return {
          ...session,
          workItem: deriveWorkItem(session.workflow, events),
        };
      }),
    );
    setSessions(enriched);
    setSelectedIds((current) => {
      const next = new Set<string>();
      for (const id of current) {
        if (enriched.some((session) => session.id === id)) next.add(id);
      }
      return next;
    });
  }, [limit, view]);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        await loadSessions();
      } catch (err) {
        if (alive) setError(err);
      }
    }
    void load();
    return () => {
      alive = false;
    };
  }, [loadSessions]);

  useEffect(() => {
    setCurrentPage(0);
    setSelectedIds(new Set());
    setFeedback(null);
  }, [view]);

  const filteredSessions = useMemo(() => {
    if (!sessions) return [];
    return sessions.filter((session) => matchesSessionSearch(session, searchQuery));
  }, [sessions, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredSessions.length / PAGE_SIZE));

  const visibleSessions = useMemo(() => {
    const start = currentPage * PAGE_SIZE;
    return filteredSessions.slice(start, start + PAGE_SIZE);
  }, [currentPage, filteredSessions]);

  useEffect(() => {
    setCurrentPage(0);
  }, [searchQuery]);

  useEffect(() => {
    if (currentPage > totalPages - 1) {
      setCurrentPage(Math.max(0, totalPages - 1));
    }
  }, [currentPage, totalPages]);

  const selectableOnPage = useMemo(
    () => visibleSessions.filter((session) => !isRunningSession(session.status)),
    [visibleSessions],
  );

  const allSelectableOnPageSelected =
    selectableOnPage.length > 0 &&
    selectableOnPage.every((session) => selectedIds.has(session.id));

  const someSelectableOnPageSelected =
    selectableOnPage.some((session) => selectedIds.has(session.id)) &&
    !allSelectableOnPageSelected;

  function toggleSessionSelection(sessionId: string, checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }

  function toggleSelectAllOnPage(checked: boolean) {
    setSelectedIds((current) => {
      const next = new Set(current);
      for (const session of selectableOnPage) {
        if (checked) next.add(session.id);
        else next.delete(session.id);
      }
      return next;
    });
  }

  async function runBulkAction(
    action: "archive" | "delete" | "restore",
    ids: readonly string[],
  ): Promise<void> {
    setActionBusy(true);
    setFeedback(null);
    const permanentDelete = action === "delete" && view !== "active";
    const results = await Promise.allSettled(
      ids.map((id) => {
        if (action === "archive") return archiveSession(id);
        if (action === "restore") return restoreSession(id);
        return deleteSession(id, { permanent: permanentDelete });
      }),
    );
    const succeeded = results.filter((result) => result.status === "fulfilled").length;
    const failed = results.length - succeeded;

    try {
      await loadSessions();
    } catch (err) {
      setFeedback({
        variant: "error",
        message:
          err instanceof Error
            ? err.message
            : "The list could not be refreshed after your request.",
      });
      setActionBusy(false);
      return;
    }

    setSelectedIds(new Set());
    setActionBusy(false);

    const actionVerb =
      action === "archive"
        ? "archived"
        : action === "restore"
          ? "restored"
          : permanentDelete
            ? "permanently deleted"
            : "moved to Recently deleted";

    if (failed === 0) {
      setFeedback({
        variant: "success",
        message: `${succeeded} workflow${succeeded === 1 ? "" : "s"} ${actionVerb}.`,
      });
      return;
    }

    if (succeeded === 0) {
      setFeedback({
        variant: "error",
        message:
          action === "restore"
            ? "None of the selected workflows could be restored."
            : "None of the selected workflows could be updated. Running workflows must finish first.",
      });
      return;
    }

    setFeedback({
      variant: "error",
      message: `${succeeded} updated, ${failed} could not be completed. Running workflows must finish first.`,
    });
  }

  async function handleArchiveSelected() {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    await runBulkAction("archive", ids);
  }

  async function handleRestoreSelected() {
    const ids = [...selectedIds];
    if (ids.length === 0) return;
    await runBulkAction("restore", ids);
  }

  async function handleDeleteConfirmed() {
    const ids = [...selectedIds];
    if (ids.length === 0) {
      setDeleteConfirmOpen(false);
      return;
    }
    setDeleteConfirmOpen(false);
    await runBulkAction("delete", ids);
  }

  function handleDownloadCleanList() {
    if (filteredSessions.length === 0) return;
    downloadSessionsCsv(filteredSessions);
  }

  if (error) return <ErrorBanner error={error} />;
  const emptyMessage =
    view === "active"
      ? "No sessions yet. Pick a workflow above to start."
      : view === "archived"
        ? "No archived workflows. Archive finished workflows from the Active tab to tidy your list."
        : "No recently deleted workflows. Deleted items stay here for 30 days before they are removed.";

  if (sessions === null) {
    return (
      <Card aria-busy="true" aria-label="Loading recent sessions">
        <RecentSessionsHeader
          view={view}
          onViewChange={setView}
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          onDownloadCleanList={handleDownloadCleanList}
          downloadDisabled
          disabled
        />
        <CardContent>
          <SkeletonTableRows rows={5} cols={5} />
        </CardContent>
      </Card>
    );
  }

  const hasSearchQuery = searchQuery.trim().length > 0;

  return (
    <Card>
      <RecentSessionsHeader
        view={view}
        onViewChange={setView}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        onDownloadCleanList={handleDownloadCleanList}
        downloadDisabled={filteredSessions.length === 0}
      />
      <CardContent>
        {sessions.length === 0 ? (
          <p className="rounded-md border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500">
            {emptyMessage}
          </p>
        ) : filteredSessions.length === 0 ? (
          <p className="rounded-md border border-dashed border-slate-300 px-4 py-8 text-center text-sm text-slate-500">
            No workflows match your search.
          </p>
        ) : (
          <>
            {selectedIds.size > 0 ? (
              <SessionSelectionBar
                view={view}
                selectedCount={selectedIds.size}
                busy={actionBusy}
                onArchive={view === "active" ? () => void handleArchiveSelected() : undefined}
                onRestore={
                  view !== "active" ? () => void handleRestoreSelected() : undefined
                }
                onDelete={() => setDeleteConfirmOpen(true)}
                onClear={() => setSelectedIds(new Set())}
              />
            ) : null}

            {feedback ? (
              <Alert variant={feedback.variant === "success" ? "success" : "error"} className="mb-3">
                {feedback.message}
              </Alert>
            ) : null}

            <Table>
              <THead>
                <TR>
                  <TH className="w-10">
                    <input
                      type="checkbox"
                      className={checkboxClassName}
                      checked={allSelectableOnPageSelected}
                      ref={(element) => {
                        if (element) element.indeterminate = someSelectableOnPageSelected;
                      }}
                      onChange={(event) => toggleSelectAllOnPage(event.target.checked)}
                      disabled={actionBusy || selectableOnPage.length === 0}
                      aria-label="Select all workflows on this page"
                    />
                  </TH>
                  <TH>{view === "deleted" ? "Removed" : "Started"}</TH>
                  <TH className="w-24">Type</TH>
                  <TH>Workflow</TH>
                  <TH className="w-12 text-center">Status</TH>
                </TR>
              </THead>
              <TBody>
                {visibleSessions.map((session) => {
                  const statusLabel = deriveWorkflowStatusLabel(session);
                  const href = `/${session.workflow}/${session.id}` as const;
                  const running = isRunningSession(session.status);
                  const checked = selectedIds.has(session.id);
                  return (
                    <TR key={session.id} aria-selected={checked}>
                      <TD>
                        <input
                          type="checkbox"
                          className={checkboxClassName}
                          checked={checked}
                          disabled={actionBusy || running}
                          onChange={(event) =>
                            toggleSessionSelection(session.id, event.target.checked)
                          }
                          aria-label={
                            running
                              ? `${session.workItem} is running and cannot be selected`
                              : `Select ${session.workItem}`
                          }
                        />
                      </TD>
                      <TD className="text-xs text-slate-700">
                        {formatTime(
                          view === "deleted" && session.deleted_at
                            ? session.deleted_at
                            : session.started_at,
                        )}
                      </TD>
                      <TD>
                        <Badge variant="neutral">
                          {workflowTypeLabel(session.workflow)}
                        </Badge>
                      </TD>
                      <TD className="max-w-sm text-sm">
                        {view === "deleted" ? (
                          <span className="font-medium text-slate-800">{session.workItem}</span>
                        ) : (
                          <Link
                            href={href}
                            className="font-medium text-slate-800 underline-offset-4 hover:text-slate-950 hover:underline"
                          >
                            {session.workItem}
                          </Link>
                        )}
                      </TD>
                      <TD className="text-center">
                        <LiveProcessBadge
                          variant="dot"
                          tone={liveTone(session.status)}
                          ariaLabel={statusLabel}
                        />
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>

            <DeleteConfirmDialog
              open={deleteConfirmOpen}
              view={view}
              selectedCount={selectedIds.size}
              busy={actionBusy}
              onCancel={() => setDeleteConfirmOpen(false)}
              onConfirm={() => void handleDeleteConfirmed()}
            />

            <SessionPagination
              currentPage={currentPage}
              totalPages={totalPages}
              onPageChange={setCurrentPage}
            />

            {hasSearchQuery ? (
              <p className="mt-3 text-center text-xs text-slate-500">
                {filteredSessions.length} workflow
                {filteredSessions.length === 1 ? "" : "s"} found
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function formatTime(iso: string): string {
  try {
    const date = new Date(iso);
    return `${date.toLocaleDateString()} ${date.toLocaleTimeString()}`;
  } catch {
    return iso;
  }
}

function escapeCsvField(value: string): string {
  if (/[",\n\r]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function buildSessionsCsv(sessions: ReadonlyArray<SessionRow>): string {
  const headers = ["Started", "Type", "Workflow", "Status", "Session ID"];
  const rows = sessions.map((session) => [
    formatTime(session.started_at),
    workflowTypeLabel(session.workflow),
    session.workItem,
    deriveWorkflowStatusLabel(session),
    session.id,
  ]);

  return [headers, ...rows]
    .map((row) => row.map(escapeCsvField).join(","))
    .join("\r\n");
}

function downloadSessionsCsv(sessions: ReadonlyArray<SessionRow>): void {
  const csv = buildSessionsCsv(sessions);
  const blob = new Blob([`\uFEFF${csv}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `agentforge-sessions-${formatDateForFilename(new Date())}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function formatDateForFilename(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

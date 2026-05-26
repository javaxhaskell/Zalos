export type WalkthroughStepId = "start" | "upload" | "describe" | "review";

export interface WalkthroughStep {
  readonly id: WalkthroughStepId;
  readonly index: number;
  readonly title: string;
  readonly description: string;
  readonly navLabel: string;
}

export const AUTHOR_WALKTHROUGH_STEPS: readonly WalkthroughStep[] = [
  {
    id: "start",
    index: 1,
    title: "Start authoring",
    description: "Open a new Author session from the dashboard.",
    navLabel: "Dashboard",
  },
  {
    id: "upload",
    index: 2,
    title: "Upload your data",
    description: "Upload a file or use the bundled expense exception review sample.",
    navLabel: "Upload",
  },
  {
    id: "describe",
    index: 3,
    title: "Describe the task",
    description: "Describe the task. Expense exception review text is pre-filled.",
    navLabel: "Describe",
  },
  {
    id: "review",
    index: 4,
    title: "Review, approve & download",
    description: "Review the results, approve flagged exceptions, and download your files.",
    navLabel: "Review",
  },
] as const;

export const EXPENSE_DEMO_DESCRIPTION =
  "Review this expense report, flag policy exceptions, and return all rows plus a separate exceptions file.";

/** @deprecated Use EXPENSE_DEMO_DESCRIPTION */
export const BANK_DEMO_DESCRIPTION = EXPENSE_DEMO_DESCRIPTION;

export const EXPENSE_DEMO_PLAN_ITEMS = [
  "Read each expense line and preserve every original column",
  "Flag policy exceptions such as over-limit amounts or missing receipts",
  "Explain exception reasons with severity and confidence",
  "Produce full output plus a separate exceptions file",
] as const;

/** @deprecated Use EXPENSE_DEMO_PLAN_ITEMS */
export const BANK_DEMO_PLAN_ITEMS = EXPENSE_DEMO_PLAN_ITEMS;

export interface ExpenseDemoFileColumnProfile {
  readonly name: string;
  readonly type: string;
  readonly nulls: string;
}

export interface ExpenseDemoFilePreviewRow {
  readonly expense_id: string;
  readonly merchant: string;
  readonly expense_date: string;
  readonly category: string;
  readonly amount: string;
}

export const EXPENSE_DEMO_FILE_PREVIEW_COLUMNS = [
  "expense_id",
  "merchant",
  "expense_date",
  "category",
  "amount",
] as const;

/** @deprecated Use EXPENSE_DEMO_FILE_PREVIEW_COLUMNS */
export const BANK_DEMO_FILE_PREVIEW_COLUMNS = EXPENSE_DEMO_FILE_PREVIEW_COLUMNS;

export const EXPENSE_DEMO_FILE = {
  filename: "expense_exception_review.csv",
  rowCount: 7,
  sizeLabel: "1 KB",
  columns: [
    { name: "expense_id", type: "Text", nulls: "0%" },
    { name: "employee_id", type: "Text", nulls: "0%" },
    { name: "expense_date", type: "Date", nulls: "0%" },
    { name: "category", type: "Text", nulls: "0%" },
    { name: "amount", type: "Decimal", nulls: "0%" },
    { name: "currency", type: "Text", nulls: "0%" },
    { name: "merchant", type: "Text", nulls: "0%" },
    { name: "approval_status", type: "Text", nulls: "0%" },
    { name: "receipt_attached", type: "Boolean", nulls: "0%" },
    { name: "policy_limit", type: "Decimal", nulls: "0%" },
    { name: "notes", type: "Text", nulls: "0%" },
  ] satisfies readonly ExpenseDemoFileColumnProfile[],
  previewRows: [
    {
      expense_id: "EXP-001",
      merchant: "City Taxi",
      expense_date: "2026-04-01",
      category: "Travel",
      amount: "120.00",
    },
    {
      expense_id: "EXP-004",
      merchant: "SaaS Co",
      expense_date: "2026-04-04",
      category: "Software",
      amount: "60.00",
    },
    {
      expense_id: "EXP-007",
      merchant: "Airline",
      expense_date: "2026-04-07",
      category: "Travel",
      amount: "500.00",
    },
  ] satisfies readonly ExpenseDemoFilePreviewRow[],
} as const;

/** @deprecated Use EXPENSE_DEMO_FILE */
export const BANK_DEMO_FILE = EXPENSE_DEMO_FILE;

export const REPAIR_WALKTHROUGH_STEPS: readonly WalkthroughStep[] = [
  {
    id: "start",
    index: 1,
    title: "Start repair",
    description: "Open a new Repair session from the dashboard.",
    navLabel: "Dashboard",
  },
  {
    id: "upload",
    index: 2,
    title: "Load your agent",
    description: "Load the bundled sample agent or upload your own as a ZIP file.",
    navLabel: "Load",
  },
  {
    id: "describe",
    index: 3,
    title: "Describe the problem",
    description:
      "Describe the problem. For the sample agent, invoices exactly 31 days overdue land in the wrong aging bucket.",
    navLabel: "Describe",
  },
  {
    id: "review",
    index: 4,
    title: "Review, approve & download",
    description:
      "Follow the workflow as it summarises the agent, reproduces the failure, proposes a fix, and asks for approval.",
    navLabel: "Review",
  },
] as const;

export const REPAIR_DEMO_DESCRIPTION =
  "Invoices exactly 31 days overdue are placed in the wrong aging bucket. They should appear in the 31-60 day bucket, not the 1-30 day bucket.";

export const REPAIR_DEMO_PLAN_ITEMS = [
  "Review how the agent assigns overdue invoices to aging buckets",
  "Reproduce the failure with invoices at the 31-day boundary",
  "Propose a fix and verify aging buckets after the change",
  "Prepare repair report and updated agent files for download",
] as const;

export const REPAIR_DEMO_AGENT = {
  name: "Invoice Aging Boundary Repair",
  filename: "invoice_aging_agent.zip",
  sizeLabel: "~64 KB",
  fileCount: 8,
  files: [
    { name: "agent.py", kind: "Agent logic" },
    { name: "config.json", kind: "Settings" },
    { name: "sample_invoices.csv", kind: "Sample data" },
    { name: "README.md", kind: "Documentation" },
    { name: "validation_rules.json", kind: "Validation rules" },
    { name: "aging_buckets.yaml", kind: "Bucket config" },
    { name: "tests/test_aging.py", kind: "Tests" },
    { name: "requirements.txt", kind: "Dependencies" },
  ],
} as const;

export type WalkthroughVariant = "author" | "repair";

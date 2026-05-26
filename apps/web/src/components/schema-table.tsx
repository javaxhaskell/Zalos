import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { TD, TH } from "@/components/ui/table";
import type { ColumnProfile, FileProfile } from "@agentforge/shared-schemas";

/**
 * Render a typed :class:`FileProfile` returned by
 * ``inspect_csv_schema`` / ``inspect_xlsx_schema``. Surfaces the
 * column name, dtype, null rate, sample values, and any
 * ``ambiguity_note`` — the latter especially important because the
 * invoice-aging-style date-format bugs we want to catch live there.
 */
export interface SchemaTableProps {
  readonly profile: FileProfile;
}

export function SchemaTable({ profile }: SchemaTableProps) {
  const displayFileName = profile.filename.replace(/_/g, " ");

  return (
    <Card className="overflow-hidden border-slate-200/90 shadow-sm">
      <CardHeader className="space-y-1 border-b border-slate-100 bg-white pb-4">
        <h3 className="text-lg font-semibold tracking-tight text-slate-900">
          File profile
        </h3>
        <p className="text-sm text-slate-600">
          {displayFileName}
          <span className="text-slate-400"> · </span>
          {profile.row_count.toLocaleString()} rows
          {profile.sheet_name ? (
            <>
              <span className="text-slate-400"> · </span>
              sheet &quot;{profile.sheet_name}&quot;
            </>
          ) : null}
        </p>
      </CardHeader>
      <CardContent className="bg-slate-50/30 p-5">
        <div
          className="overflow-auto rounded-lg border border-slate-200 bg-white shadow-sm"
          style={{ maxHeight: "min(28rem, 55vh)" }}
        >
          <table className="w-full min-w-max border-collapse text-sm">
            <thead className="sticky top-0 z-10 border-b border-slate-200 bg-white">
              <tr>
                <TH className="whitespace-nowrap px-4 py-3 text-xs font-medium normal-case text-slate-500">
                  Column
                </TH>
                <TH className="w-28 whitespace-nowrap px-4 py-3 text-xs font-medium normal-case text-slate-500">
                  Type
                </TH>
                <TH className="w-28 whitespace-nowrap px-4 py-3 text-right text-xs font-medium normal-case text-slate-500">
                  Null rate
                </TH>
                <TH className="whitespace-nowrap px-4 py-3 text-xs font-medium normal-case text-slate-500">
                  Sample values
                </TH>
                <TH className="whitespace-nowrap px-4 py-3 text-xs font-medium normal-case text-slate-500">
                  Note
                </TH>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {profile.columns.map((col: ColumnProfile) => (
                <tr
                  key={col.name}
                  className="transition-colors hover:bg-slate-50/80"
                >
                  <TD className="px-4 py-3 font-medium text-slate-900">
                    {col.name}
                  </TD>
                  <TD className="px-4 py-3">
                    <Badge variant="neutral">{col.dtype}</Badge>
                  </TD>
                  <TD className="px-4 py-3 text-right tabular-nums text-slate-800">
                    {formatPct(col.null_rate)}
                  </TD>
                  <TD
                    className="max-w-[14rem] px-4 py-3 text-slate-800"
                    title={col.sample_values.join(", ")}
                  >
                    <span className="block truncate">
                      {col.sample_values.slice(0, 3).join(", ")}
                    </span>
                  </TD>
                  <TD className="px-4 py-3">
                    {col.ambiguity_note ? (
                      <Badge variant="warning" title={col.ambiguity_note}>
                        ambiguous
                      </Badge>
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </TD>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {profile.columns.some((c: ColumnProfile) => c.ambiguity_note) ? (
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-amber-800">
            {profile.columns
              .filter((c: ColumnProfile) => c.ambiguity_note)
              .map((c: ColumnProfile) => (
                <li key={c.name}>
                  <strong>{c.name}</strong>: {c.ambiguity_note}
                </li>
              ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

function formatPct(rate: number): string {
  if (rate === 0) return "0%";
  if (rate < 0.001) return "<0.1%";
  return `${(rate * 100).toFixed(1)}%`;
}

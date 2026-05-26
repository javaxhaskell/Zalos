import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import path from "node:path";

const SAMPLE_FILENAME = "expense_exception_review.csv";

function resolveSamplePath(): string {
  const candidates = [
    path.join(
      process.cwd(),
      "app",
      "api",
      "reference-samples",
      "expense-exception-review",
      SAMPLE_FILENAME,
    ),
    path.join(
      process.cwd(),
      "apps",
      "web",
      "app",
      "api",
      "reference-samples",
      "expense-exception-review",
      SAMPLE_FILENAME,
    ),
  ];

  const match = candidates.find((candidate) => existsSync(candidate));
  if (!match) {
    throw new Error(
      `Expense exception review reference sample CSV not found. Tried: ${candidates.join(", ")}`,
    );
  }
  return match;
}

export async function GET() {
  try {
    const data = await readFile(resolveSamplePath());
    return new Response(data, {
      headers: {
        "cache-control": "no-store",
        "content-disposition": `attachment; filename="${SAMPLE_FILENAME}"`,
        "content-type": "text/csv; charset=utf-8",
      },
    });
  } catch {
    return new Response(
      "Bundled expense exception review sample is unavailable.",
      {
        status: 404,
        headers: { "content-type": "text/plain; charset=utf-8" },
      },
    );
  }
}

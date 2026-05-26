"use client";

import { useEffect, useState } from "react";

import { ErrorBanner } from "@/components/error-banner";
import { SkeletonCard } from "@/components/motion";
import { fetchArtifactText } from "@/lib/api-client";

/**
 * Minimal markdown renderer for the validation + repair reports.
 *
 * Intentionally hand-rolled instead of pulling in ``react-markdown``:
 *
 *   * We control the markdown source (the reports' templates live in
 *     ``apps/api/src/agentforge/validation/reporter.py`` and
 *     ``validation/repair.py``), so the syntax surface is small and
 *     known: ``#`` / ``##`` / ``###`` headings, ``**bold**``,
 *     ``- bullets``, fenced code blocks, paragraphs.
 *   * INV-10 — the report is data the agent produced, not
 *     instructions. The renderer NEVER uses ``dangerouslySetInnerHTML``;
 *     every line lands as a React element with literal text content.
 *   * Avoids a dep + bundle-size bump just for one well-controlled
 *     surface. If we need full markdown later (user-authored notes,
 *     etc.) switch to ``react-markdown`` and delete this.
 */
export interface MarkdownViewerProps {
  readonly sessionId: string;
  readonly relativePath: string;
}

export function MarkdownViewer({ sessionId, relativePath }: MarkdownViewerProps) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let alive = true;
    setContent(null);
    setError(null);
    fetchArtifactText(sessionId, relativePath)
      .then((text) => {
        if (alive) setContent(text);
      })
      .catch((err) => {
        if (alive) setError(err);
      });
    return () => {
      alive = false;
    };
  }, [sessionId, relativePath]);

  if (error) return <ErrorBanner error={error} />;
  if (content === null) {
    return <SkeletonCard lines={5} className="border-slate-200 shadow-none" />;
  }
  return (
    <article className="rounded-md border border-slate-200 bg-white px-4 py-3 text-sm text-slate-800">
      {renderMarkdown(content)}
    </article>
  );
}

/**
 * Render a known-subset markdown string to React elements.
 *
 * Supports: `#` / `##` / `###` headings, paragraphs, `-` / `*`
 * bullet lists, fenced code blocks (```...```), and inline
 * ``**bold**``. Unknown constructs degrade to plain text.
 */
function renderMarkdown(markdown: string): React.ReactNode {
  const lines = markdown.split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";

    // Fenced code block
    if (line.startsWith("```")) {
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) {
        codeLines.push(lines[i] ?? "");
        i += 1;
      }
      // Skip the closing fence if present.
      if (i < lines.length) i += 1;
      blocks.push(
        <pre
          key={key++}
          className="mt-2 overflow-x-auto rounded bg-slate-100 px-3 py-2 font-mono text-xs"
        >
          {codeLines.join("\n")}
        </pre>,
      );
      continue;
    }

    // Headings
    if (line.startsWith("### ")) {
      blocks.push(
        <h4
          key={key++}
          className="mt-4 text-sm font-semibold text-slate-900"
        >
          {renderInline(line.slice(4))}
        </h4>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      blocks.push(
        <h3
          key={key++}
          className="mt-5 text-base font-semibold text-slate-900"
        >
          {renderInline(line.slice(3))}
        </h3>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith("# ")) {
      blocks.push(
        <h2
          key={key++}
          className="mt-5 text-lg font-semibold text-slate-900"
        >
          {renderInline(line.slice(2))}
        </h2>,
      );
      i += 1;
      continue;
    }

    // Bullet list (consume contiguous `-` / `*` lines)
    if (line.startsWith("- ") || line.startsWith("* ")) {
      const items: string[] = [];
      while (
        i < lines.length &&
        ((lines[i] ?? "").startsWith("- ") ||
          (lines[i] ?? "").startsWith("* "))
      ) {
        items.push((lines[i] ?? "").slice(2));
        i += 1;
      }
      blocks.push(
        <ul key={key++} className="mt-2 list-disc space-y-1 pl-5">
          {items.map((it, idx) => (
            <li key={idx}>{renderInline(it)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Blank line — paragraph break
    if (line.trim() === "") {
      i += 1;
      continue;
    }

    // Plain paragraph (consume contiguous non-empty, non-special lines)
    const paraLines: string[] = [line];
    i += 1;
    while (i < lines.length) {
      const next = lines[i] ?? "";
      if (
        next.trim() === "" ||
        next.startsWith("#") ||
        next.startsWith("- ") ||
        next.startsWith("* ") ||
        next.startsWith("```")
      ) {
        break;
      }
      paraLines.push(next);
      i += 1;
    }
    blocks.push(
      <p key={key++} className="mt-2 leading-relaxed">
        {renderInline(paraLines.join(" "))}
      </p>,
    );
  }
  return blocks;
}

/**
 * Render inline ``**bold**`` markers. Everything else is literal text.
 */
function renderInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>;
    }
    return <span key={idx}>{part}</span>;
  });
}

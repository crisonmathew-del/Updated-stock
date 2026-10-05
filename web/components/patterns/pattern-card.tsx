"use client";

import Image from "next/image";
import type { Pattern } from "@/lib/api";
import { formatPrice, patternStatus } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_CLASS: Record<Pattern["status"], string> = {
  forming: "text-foreground",
  broken_out: "text-ok",
  failed: "text-fail",
  expired: "text-muted",
};

/** Facts, chart and quality breakdown of one detection. `children` adds controls (verdicts). */
export function PatternCard({
  pattern,
  showChart = true,
  children,
}: {
  pattern: Pattern;
  showChart?: boolean;
  children?: React.ReactNode;
}) {
  const p = pattern;
  const facts = [
    `${p.start_date} → ${p.end_date}`,
    `${p.duration_weeks.toFixed(1)} weeks`,
    p.depth_pct != null ? `${p.depth_pct.toFixed(1)}% deep` : null,
    `pivot ${formatPrice(p.pivot)}`,
    p.base_number != null ? `base ${p.base_number}` : null,
  ].filter(Boolean);
  return (
    <article
      aria-label={`${p.symbol} ${p.type_label}`}
      className="flex flex-col gap-3 rounded-md border border-border p-4"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-medium">
          {p.symbol} <span className="font-normal text-muted">· {p.type_label}</span>
        </h3>
        <p className="tabular text-sm">
          <span className={cn(STATUS_CLASS[p.status])}>{patternStatus(p.status)}</span>
          <span className="text-muted"> · quality </span>
          {p.quality.toFixed(0)}/100
        </p>
      </header>
      <p className="tabular text-sm text-muted">{facts.join(" · ")}</p>
      {showChart && (
        <Image
          src={`/api/admin/patterns/${p.id}/chart.png`}
          alt={`Chart of ${p.symbol}: ${p.type_label} from ${p.start_date} to ${p.end_date}, pivot ${formatPrice(p.pivot)}`}
          width={1000}
          height={600}
          unoptimized
          className="h-auto w-full rounded border border-border"
        />
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted">Quality breakdown</summary>
        <ul className="mt-2 divide-y divide-border">
          {p.components.map((c) => (
            <li key={c.key} className="flex gap-3 py-1.5">
              <span className="flex-1">
                {c.label}
                <span className="tabular block text-xs text-muted">{c.detail}</span>
              </span>
              <span className="tabular text-muted">
                {c.points.toFixed(1)}/{c.max_points}
              </span>
            </li>
          ))}
        </ul>
      </details>
      {children}
    </article>
  );
}

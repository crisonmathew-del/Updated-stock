"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import {
  api,
  type FiscalPeriod,
  type Fundamentals,
  type Note,
  type Peer,
  type SetupDetail,
  type StockSummary,
} from "@/lib/api";
import { formatCompact, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useListStore } from "@/stores/list";
import { Section, StatusMark } from "@/components/ui/section";

export function TrendTemplatePanel({ summary }: { summary: StockSummary }) {
  return (
    <Section
      title="Trend Template"
      note={`${summary.trend_template_passed}/8 · ${summary.stage_label ?? "stage unknown"}`}
    >
      <ul className="flex flex-col gap-1.5 text-sm">
        {summary.checks.map((c) => (
          <li key={c.key} className="flex gap-2">
            <StatusMark status={c.passed} />
            <span className="min-w-0">
              {c.label}
              <span className="block text-xs text-muted">{c.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      {summary.checks.length === 0 && (
        <p className="text-sm text-muted">Needs 200 sessions of history to evaluate.</p>
      )}
    </Section>
  );
}

/**
 * Quarterly bars for one measure (EPS or sales): bar height is the value, the label under it
 * the year-over-year growth. Losses hang below the baseline. One series, so no legend.
 */
function QuarterBars({
  periods,
  pick,
  growth,
  name,
}: {
  periods: FiscalPeriod[];
  pick: (p: FiscalPeriod) => number | null;
  growth: (p: FiscalPeriod) => number | null;
  name: string;
}) {
  const shown = [...periods].sort((a, b) => a.period_end.localeCompare(b.period_end)).slice(-8);
  const values = shown.map(pick);
  const finite = values.filter((v): v is number => v != null);
  if (finite.length === 0) return <p className="text-xs text-muted">No {name} reported.</p>;
  const top = Math.max(0, ...finite);
  const bottom = Math.min(0, ...finite);
  const span = top - bottom || 1;
  const W = 28;
  const H = 64;
  const zero = (top / span) * H;
  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="text-xs text-muted">
        {name}, last {shown.length} quarters
      </figcaption>
      <svg
        viewBox={`0 0 ${shown.length * W} ${H + 18}`}
        className="h-[82px] w-full"
        role="img"
        aria-label={`${name} by quarter: ${shown
          .map((p, i) => `${p.label} ${values[i] ?? "n/a"}`)
          .join(", ")}`}
      >
        <line x1={0} x2={shown.length * W} y1={zero} y2={zero} stroke="var(--border)" />
        {shown.map((p, i) => {
          const v = values[i];
          const g = growth(p);
          const h = v == null ? 0 : (Math.abs(v) / span) * H;
          const y = v != null && v < 0 ? zero : zero - h;
          return (
            <g key={p.period_end}>
              <title>{`${p.label}: ${v ?? "n/a"}${g != null ? `, ${g > 0 ? "+" : ""}${g.toFixed(0)}% y/y` : ""}`}</title>
              <rect
                x={i * W + 5}
                y={y}
                width={W - 10}
                height={Math.max(h, 1)}
                rx={2}
                fill={v != null && v < 0 ? "var(--fall)" : "var(--rise)"}
                opacity={i === shown.length - 1 ? 1 : 0.55}
              />
              <text
                x={i * W + W / 2}
                y={H + 14}
                textAnchor="middle"
                fontSize={9}
                fill={g == null ? "var(--muted)" : g >= 0 ? "var(--rise)" : "var(--fall)"}
              >
                {g == null ? "–" : `${g > 0 ? "+" : ""}${g.toFixed(0)}%`}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export function FundamentalsPanel({ symbol }: { symbol: string }) {
  const f = useQuery({
    queryKey: ["stock", symbol, "fundamentals"],
    queryFn: () => api.get<Fundamentals>(`/api/stocks/${symbol}/fundamentals`),
    staleTime: 10 * 60_000,
  });
  const grade = f.data?.grade;
  return (
    <Section
      title="Fundamentals"
      note={grade ? <GradeBadge grade={grade.grade} score={grade.score} /> : undefined}
    >
      {f.isPending && <p className="text-sm text-muted">Loading…</p>}
      {f.data && f.data.quarters.length === 0 && (
        <p className="text-sm text-muted">
          No statements loaded for {symbol}. They come from SEC EDGAR (make fundamentals).
        </p>
      )}
      {f.data && f.data.quarters.length > 0 && (
        <>
          <QuarterBars
            periods={f.data.quarters}
            pick={(p) => p.eps}
            growth={(p) => p.eps_growth_pct}
            name="EPS"
          />
          <QuarterBars
            periods={f.data.quarters}
            pick={(p) => p.revenue}
            growth={(p) => p.revenue_growth_pct}
            name="Sales"
          />
        </>
      )}
      {grade && (
        <ul className="flex flex-col gap-1 border-t border-border pt-2 text-sm">
          {grade.components.map((c) => (
            <li key={c.key} className="flex gap-2" title={c.detail}>
              <StatusMark status={c.status as "pass" | "partial" | "fail" | "no_data"} />
              <span className="min-w-0 flex-1 truncate">{c.label}</span>
              <span className="tabular text-muted">
                {c.points.toFixed(0)}/{c.max_points}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

export function PatternPanel({ setup }: { setup: SetupDetail | null | undefined }) {
  const p = setup?.pattern;
  return (
    <Section
      title="Pattern"
      note={p ? `${p.type_label} · quality ${p.quality.toFixed(0)}/100` : undefined}
    >
      {!p && <p className="text-sm text-muted">No base detected.</p>}
      {p && (
        <>
          <p className="tabular text-sm text-muted">
            {p.start_date} → {p.end_date} · {p.duration_weeks.toFixed(1)} weeks
            {p.depth_pct != null ? ` · ${p.depth_pct.toFixed(1)}% deep` : ""} · pivot{" "}
            {formatPrice(p.pivot)}
            {p.base_number != null ? ` · base ${p.base_number}` : ""}
          </p>
          <ul className="flex flex-col gap-1 text-sm">
            {p.components.map((c) => (
              <li key={c.key} className="flex gap-2" title={c.detail}>
                <StatusMark
                  status={
                    c.points >= c.max_points - 1e-9 ? "pass" : c.points > 0 ? "partial" : "fail"
                  }
                />
                <span className="min-w-0 flex-1">
                  {c.label}
                  <span className="block text-xs text-muted">{c.detail}</span>
                </span>
                <span className="tabular text-muted">
                  {c.points.toFixed(1)}/{c.max_points}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      {setup && setup.transitions.length > 0 && (
        <div className="flex flex-col gap-1 border-t border-border pt-2">
          <h3 className="text-xs text-muted">Stage history</h3>
          <ol className="flex flex-col gap-1 text-sm">
            {setup.transitions.map((t, i) => (
              <li key={i}>
                <span className="tabular text-muted">{t.date}</span>{" "}
                <StageBadge state={t.to_state} />
                <span className="block text-xs text-muted">{t.reason}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </Section>
  );
}

export function PeersPanel({ symbol, summary }: { symbol: string; summary: StockSummary }) {
  const setList = useListStore((s) => s.setList);
  const peers = useQuery({
    queryKey: ["stock", symbol, "peers"],
    queryFn: () => api.get<Peer[]>(`/api/stocks/${symbol}/peers`),
    staleTime: 10 * 60_000,
  });
  const g = summary.group;
  return (
    <Section
      title="Group & peers"
      note={g ? `${g.name} · rank ${g.rank ?? "–"} of ${g.ranked_groups ?? "–"}` : undefined}
    >
      {!g && <p className="text-sm text-muted">No industry group (no SEC industry code).</p>}
      {peers.data && peers.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-xs text-muted">
            <tr className="text-left">
              <th className="py-1 font-normal">Stock</th>
              <th className="py-1 text-right font-normal">Close</th>
              <th className="py-1 text-right font-normal">RS</th>
              <th className="py-1 text-right font-normal">Grade</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {peers.data.map((p) => (
              <tr key={p.symbol} className={cn(p.is_self && "font-semibold")}>
                <td className="py-1">
                  <Link
                    href={`/stocks/${p.symbol}`}
                    onClick={() =>
                      setList(
                        `${g?.name ?? "Group"} peers`,
                        peers.data.map((x) => x.symbol),
                      )
                    }
                    className="hover:underline"
                  >
                    {p.symbol}
                  </Link>{" "}
                  {p.state && <StageBadge state={p.state} className="text-xs" />}
                </td>
                <td className="py-1 text-right">
                  {formatPrice(p.close)} <Change value={p.change_pct} className="text-xs" />
                </td>
                <td className="py-1 text-right">{p.rs_rating ?? "—"}</td>
                <td className="py-1 text-right">
                  <GradeBadge grade={p.grade} score={p.score} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

export function InsidersPanel({ symbol }: { symbol: string }) {
  const f = useQuery({
    queryKey: ["stock", symbol, "fundamentals"],
    queryFn: () => api.get<Fundamentals>(`/api/stocks/${symbol}/fundamentals`),
    staleTime: 10 * 60_000,
  });
  const trades = f.data?.insiders.slice(0, 8) ?? [];
  return (
    <Section title="Insider trades" note="open-market, Form 4">
      {trades.length === 0 && <p className="text-sm text-muted">None reported in the last year.</p>}
      <ul className="flex flex-col gap-1 text-sm">
        {trades.map((t, i) => (
          <li key={i} className="flex gap-2">
            <span className={t.code === "P" ? "text-rise" : "text-fall"}>
              {t.code === "P" ? "▲ Buy" : "▼ Sell"}
            </span>
            <span className="min-w-0 flex-1 truncate">
              {t.insider_name} <span className="text-muted">· {t.role}</span>
            </span>
            <span className="tabular text-muted">
              {formatCompact(t.shares)} @ {formatPrice(t.price)} · {t.transaction_date}
            </span>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function NotesPanel({ symbol }: { symbol: string }) {
  const client = useQueryClient();
  const note = useQuery({
    queryKey: ["stock", symbol, "note"],
    queryFn: () => api.get<Note>(`/api/stocks/${symbol}/note`),
  });
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (body: string) => api.put<Note>(`/api/stocks/${symbol}/note`, { body }),
    onSuccess: (saved) => {
      client.setQueryData(["stock", symbol, "note"], saved);
      setDraft(null);
    },
  });
  const value = draft ?? note.data?.body ?? "";
  return (
    <Section
      title="My notes"
      note={
        note.data?.updated_at
          ? `saved ${new Date(note.data.updated_at).toLocaleString()}`
          : undefined
      }
    >
      <label className="sr-only" htmlFor={`note-${symbol}`}>
        Notes on {symbol}
      </label>
      <textarea
        id={`note-${symbol}`}
        value={value}
        onChange={(e) => setDraft(e.target.value)}
        rows={4}
        placeholder="Why it's on your radar, what you're waiting for…"
        className="w-full resize-y rounded-md border border-border bg-background p-2 text-sm"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={draft === null || save.isPending}
          onClick={() => save.mutate(value)}
          className="rounded-md border border-border px-3 py-1 text-sm disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save note"}
        </button>
        {save.error && <span className="text-sm text-fail">{save.error.message}</span>}
      </div>
    </Section>
  );
}

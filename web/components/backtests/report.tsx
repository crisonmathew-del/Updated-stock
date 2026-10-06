"use client";

import { useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { Change, GradeBadge } from "@/components/ui/badges";
import { Section } from "@/components/ui/section";
import {
  api,
  type BacktestDetail,
  type BacktestMetrics,
  type BacktestReport,
  type BacktestTrade,
  type BreakdownRow,
  type HeatmapCell,
  type TradeChart as TradeChartData,
} from "@/lib/api";
import { formatNumber, formatPrice, formatR } from "@/lib/format";
import { patternLabel } from "@/lib/stages";
import { cn } from "@/lib/utils";
import { money, num, pct, progressText, regimeLabel } from "./format";
import { pollWhileActive, runQuery, tradeChartPath } from "./queries";
import { StatusLabel } from "./run-list";

const EquityChart = dynamic(() => import("./equity-chart"), {
  ssr: false,
  loading: () => <div className="h-[380px] animate-pulse rounded bg-surface-2" />,
});
const TradeChart = dynamic(() => import("./trade-chart"), {
  ssr: false,
  loading: () => <div className="h-[340px] animate-pulse rounded bg-surface-2" />,
});

const SIGNAL_NAMES: Record<string, string> = {
  new_top_setup: "New A/A+ setups",
  near_pivot: "Near pivot",
  breakout: "Breakouts confirmed",
  breakout_rejected: "Breakouts rejected",
  extended: "Extended",
  failed: "Breakouts failed",
  invalidated: "Setups invalidated",
  pocket_pivot: "Pocket pivots",
  earnings_gap: "Earnings gaps",
  rs_new_high_ahead: "RS line new highs ahead of price",
  pullback: "Pullback buy points",
  undercut_rally: "Undercut & rally",
};

function Tile({
  label,
  children,
  note,
}: {
  label: string;
  children: React.ReactNode;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-border bg-surface-2 px-3 py-2">
      <span className="text-xs text-muted">{label}</span>
      <span className="tabular text-lg">{children}</span>
      {note && <span className="text-xs text-muted">{note}</span>}
    </div>
  );
}

function Headline({ s }: { s: BacktestMetrics }) {
  return (
    <div
      role="group"
      aria-label="Headline numbers"
      className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6"
    >
      <Tile label="Total return" note={`SPY ${pct(s.benchmark_total_return_pct)}`}>
        <Change value={s.total_return_pct} digits={1} />
      </Tile>
      <Tile label="CAGR" note={`SPY ${pct(s.benchmark_cagr_pct)}`}>
        <Change value={s.cagr_pct} digits={1} />
      </Tile>
      <Tile
        label="Max drawdown"
        note={s.max_drawdown_peak ? `${s.max_drawdown_peak} → ${s.max_drawdown_trough}` : undefined}
      >
        {pct(s.max_drawdown_pct)}
      </Tile>
      <Tile label="Sharpe · Sortino">
        {num(s.sharpe)} · {num(s.sortino)}
      </Tile>
      <Tile label="Trades" note={`${pct(s.exposure_pct, 0)} invested on average`}>
        {formatNumber(s.trades)}
      </Tile>
      <Tile label="Win rate" note={`${s.wins} won · ${s.losses} lost`}>
        {pct(s.win_rate_pct, 1)}
      </Tile>
      <Tile label="Average win · loss">
        <span className="text-base">
          <Change value={s.avg_win_pct} digits={1} /> · <Change value={s.avg_loss_pct} digits={1} />
        </span>
      </Tile>
      <Tile label="Payoff ratio">{num(s.payoff_ratio)}</Tile>
      <Tile label="Expectancy" note="Average R per trade">
        {s.expectancy_r == null ? "—" : formatR(s.expectancy_r)}
      </Tile>
      <Tile label="Profit factor">{num(s.profit_factor)}</Tile>
      <Tile label="Net profit">{money(s.net_profit)}</Tile>
      <Tile label="Average hold" note={`${pct(s.stopped_pct, 0)} stopped out`}>
        {s.avg_sessions == null ? "—" : `${s.avg_sessions} sessions`}
      </Tile>
    </div>
  );
}

const SAMPLE_ROWS: [keyof BacktestMetrics, string, (v: never) => React.ReactNode][] = [
  ["cagr_pct", "CAGR", (v: number | null) => pct(v)],
  ["max_drawdown_pct", "Max drawdown", (v: number | null) => pct(v)],
  ["sharpe", "Sharpe", (v: number | null) => num(v)],
  ["trades", "Trades", (v: number) => formatNumber(v)],
  ["win_rate_pct", "Win rate", (v: number | null) => pct(v)],
  ["expectancy_r", "Expectancy", (v: number | null) => (v == null ? "—" : formatR(v) || "0.00R")],
  ["profit_factor", "Profit factor", (v: number | null) => num(v)],
];

function Samples({ report }: { report: BacktestReport }) {
  const { samples } = report;
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">In-sample against out-of-sample results</caption>
      <thead className="text-left text-xs text-muted">
        <tr>
          <th className="py-1 font-normal" />
          <th className="py-1 text-right font-normal">
            In sample · first {samples.split_pct}%
            <div>
              {samples.in.start} to {samples.in.end}
            </div>
          </th>
          <th className="py-1 text-right font-normal">
            Out of sample · last {100 - samples.split_pct}%
            <div>
              {samples.out.start} to {samples.out.end}
            </div>
          </th>
        </tr>
      </thead>
      <tbody>
        {SAMPLE_ROWS.map(([key, label, show]) => (
          <tr key={key} className="border-t border-border">
            <td className="py-1.5 text-muted">{label}</td>
            <td className="tabular py-1.5 text-right">{show(samples.in[key] as never)}</td>
            <td className="tabular py-1.5 text-right">{show(samples.out[key] as never)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Breakdown({
  title,
  rows,
  label = (k) => k,
}: {
  title: string;
  rows: BreakdownRow[];
  label?: (key: string) => string;
}) {
  return (
    <Section title={title}>
      {rows.length === 0 ? (
        <p className="text-sm text-muted">No trades.</p>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-muted">
            <tr>
              <th className="py-1 font-normal" />
              <th className="py-1 text-right font-normal">Trades</th>
              <th className="py-1 text-right font-normal">Win rate</th>
              <th className="py-1 text-right font-normal">Expectancy</th>
              <th className="py-1 text-right font-normal">Net</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-border">
                <td className="py-1.5 pr-2">{label(r.key)}</td>
                <td className="tabular py-1.5 text-right">{r.trades}</td>
                <td className="tabular py-1.5 text-right">{pct(r.win_rate_pct, 0)}</td>
                <td className="tabular py-1.5 text-right">
                  {r.expectancy_r == null ? "—" : formatR(r.expectancy_r)}
                </td>
                <td className="tabular py-1.5 text-right">{money(r.net_profit)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

type HeatMetric = "cagr_pct" | "expectancy_r" | "max_drawdown_pct" | "trades";
const HEAT_METRICS: { key: HeatMetric; label: string }[] = [
  { key: "cagr_pct", label: "CAGR" },
  { key: "expectancy_r", label: "Expectancy (R)" },
  { key: "max_drawdown_pct", label: "Max drawdown" },
  { key: "trades", label: "Trades" },
];

/** Diverging around zero for returns (Fall ↔ Rise), one hue for counts; the number is always
 * printed so the colour is never the only cue. */
export function heatStyle(metric: HeatMetric, value: number | null, scale: number) {
  if (value == null || scale === 0) return {};
  const share = Math.min(1, Math.abs(value) / scale);
  const strength = Math.round(12 + share * 43); // 12-55%: text stays readable
  const tone = metric === "trades" ? "--grade-b" : value >= 0 ? "--rise" : "--fall";
  return { backgroundColor: `color-mix(in srgb, var(${tone}) ${strength}%, var(--surface))` };
}

function Heatmap({ report }: { report: BacktestReport }) {
  const [metric, setMetric] = useState<HeatMetric>("cagr_pct");
  const h = report.heatmap;
  if (!h) return null;
  const values = h.cells.flat().map((c) => (c ? (c[metric] as number | null) : null));
  const scale = Math.max(0, ...values.map((v) => Math.abs(v ?? 0)));
  const show = (c: HeatmapCell | null) => {
    const v = c ? (c[metric] as number | null) : null;
    if (v == null) return "—";
    if (metric === "trades") return formatNumber(v);
    if (metric === "expectancy_r") return formatR(v) || "0.00R";
    return pct(v);
  };
  return (
    <Section
      title="Sensitivity"
      note="Does the result depend on one lucky setting? Each cell re-runs the whole backtest."
    >
      <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="Heatmap metric">
        {HEAT_METRICS.map((m) => (
          <button
            key={m.key}
            type="button"
            role="radio"
            aria-checked={metric === m.key}
            onClick={() => setMetric(m.key)}
            className={cn(
              "rounded-md border px-2 py-0.5 text-xs",
              metric === m.key ? "border-tide text-foreground" : "border-border text-muted",
            )}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="text-sm">
          <caption className="sr-only">
            {HEAT_METRICS.find((m) => m.key === metric)?.label} by VCP final contraction (rows) and
            breakout volume (columns)
          </caption>
          <thead>
            <tr className="text-xs text-muted">
              <th className="px-2 py-1 text-left font-normal">VCP final ≤ · volume ≥</th>
              {h.volume.map((v) => (
                <th key={v} scope="col" className="px-2 py-1 text-right font-normal">
                  {v}%
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {h.vcp.map((vcp, i) => (
              <tr key={vcp}>
                <th scope="row" className="px-2 py-1 text-left text-xs font-normal text-muted">
                  {vcp}%
                </th>
                {h.volume.map((volume, j) => {
                  const cell = h.cells[i]?.[j] ?? null;
                  const base = h.base.vcp === vcp && h.base.volume === volume;
                  const v = cell ? (cell[metric] as number | null) : null;
                  return (
                    <td
                      key={volume}
                      style={heatStyle(metric, v, scale)}
                      className={cn(
                        "tabular min-w-16 border-2 border-background px-2 py-1.5 text-right",
                        base && "outline-2 -outline-offset-2 outline-tide",
                      )}
                      title={
                        cell
                          ? `VCP ≤ ${vcp}%, volume ≥ ${volume}%: CAGR ${pct(cell.cagr_pct)}, ` +
                            `max drawdown ${pct(cell.max_drawdown_pct)}, ${cell.trades} trades, ` +
                            `expectancy ${num(cell.expectancy_r)}R`
                          : undefined
                      }
                    >
                      {show(cell)}
                      {base && <span className="sr-only"> (your settings)</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted">
        Outlined: your current settings (VCP final contraction ≤ {h.base.vcp}%, breakout volume ≥{" "}
        {h.base.volume}%). Blue cells are gains and orange losses; stronger colour, larger size.
      </p>
    </Section>
  );
}

function TradeRow({
  t,
  selected,
  onSelect,
}: {
  t: BacktestTrade;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <tr
      className={cn("cursor-pointer border-t border-border", selected && "bg-surface-2")}
      onClick={onSelect}
    >
      <td className="tabular py-1.5 pr-2 text-muted">{t.n}</td>
      <td className="py-1.5 pr-2">
        <button
          type="button"
          className="font-medium hover:underline"
          aria-pressed={selected}
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
        >
          {t.symbol}
        </button>
      </td>
      <td className="py-1.5 pr-2 whitespace-nowrap">{patternLabel(t.pattern)}</td>
      <td className="py-1.5 pr-2">
        <GradeBadge grade={t.grade} score={t.score} />
      </td>
      <td className="tabular py-1.5 pr-2 whitespace-nowrap">
        {t.entry_date} · {formatPrice(t.entry_price)}
      </td>
      <td className="tabular py-1.5 pr-2 whitespace-nowrap">
        {t.exit_date} · {formatPrice(t.exit_price)}
      </td>
      <td className="py-1.5 pr-2 text-xs text-muted">
        {t.exit_reason}
        {t.partial ? " (after a partial sale)" : ""}
      </td>
      <td className="tabular py-1.5 pr-2 text-right">{t.sessions}</td>
      <td className="tabular py-1.5 pr-2 text-right">
        <Change value={t.pnl_pct} digits={1} />
      </td>
      <td className="tabular py-1.5 pr-2 text-right">{formatR(t.r) || "0.00R"}</td>
      <td className="py-1.5 text-xs text-muted">{t.sample === "in" ? "In" : "Out"}</td>
    </tr>
  );
}

function SelectedTrade({ runId, trade }: { runId: number; trade: BacktestTrade }) {
  const chart = useQuery({
    queryKey: ["backtests", runId, "trade", trade.n],
    queryFn: () => api.get<TradeChartData>(tradeChartPath(runId, trade.n)),
    staleTime: Infinity,
  });
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span>
          <span className="font-semibold">
            Trade {trade.n}: {trade.symbol}
          </span>{" "}
          <span className="text-muted">
            {trade.shares} shares at {formatPrice(trade.entry_price)}, stop{" "}
            {formatPrice(trade.stop)} · signal on {trade.signal_date} ·{" "}
            {regimeLabel(trade.regime ?? "unknown")}
          </span>
        </span>
        <Link href={`/stocks/${trade.symbol}`} className="text-tide-ink hover:underline">
          Open {trade.symbol}
        </Link>
      </div>
      <ul className="text-xs text-muted">
        {trade.exits.map((f) => (
          <li key={`${f.date}-${f.reason}`}>
            {f.date}: sold {f.shares} at {formatPrice(f.price)} ({f.reason})
          </li>
        ))}
      </ul>
      {chart.error ? (
        <p role="alert" className="text-sm text-fall">
          {chart.error.message}
        </p>
      ) : chart.data ? (
        <TradeChart data={chart.data} />
      ) : (
        <div className="h-[340px] animate-pulse rounded bg-surface-2" />
      )}
    </div>
  );
}

const PAGE = 100;

function Trades({ runId, trades }: { runId: number; trades: BacktestTrade[] }) {
  const [selected, setSelected] = useState<number | null>(null);
  const [shown, setShown] = useState(PAGE);
  const pick = trades.find((t) => t.n === selected);
  return (
    <Section title="Trades" note="Select a trade to see it on its chart">
      {pick && <SelectedTrade runId={runId} trade={pick} />}
      {trades.length === 0 ? (
        <p className="text-sm text-muted">
          No trades: no setup met the rules, or none reached its entry. Try a lower grade or a
          longer period.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Every simulated trade</caption>
            <thead className="text-left text-xs text-muted">
              <tr>
                <th className="py-1 pr-2 font-normal">#</th>
                <th className="py-1 pr-2 font-normal">Stock</th>
                <th className="py-1 pr-2 font-normal">Pattern</th>
                <th className="py-1 pr-2 font-normal">Grade</th>
                <th className="py-1 pr-2 font-normal">Entry</th>
                <th className="py-1 pr-2 font-normal">Exit</th>
                <th className="py-1 pr-2 font-normal">Why it ended</th>
                <th className="py-1 pr-2 text-right font-normal">Sessions</th>
                <th className="py-1 pr-2 text-right font-normal">P&amp;L</th>
                <th className="py-1 pr-2 text-right font-normal">R</th>
                <th className="py-1 font-normal">Sample</th>
              </tr>
            </thead>
            <tbody>
              {trades.slice(0, shown).map((t) => (
                <TradeRow
                  key={t.n}
                  t={t}
                  selected={t.n === selected}
                  onSelect={() => setSelected(t.n === selected ? null : t.n)}
                />
              ))}
            </tbody>
          </table>
          {shown < trades.length && (
            <button
              type="button"
              onClick={() => setShown(shown + PAGE)}
              className="mt-2 text-sm text-tide-ink hover:underline"
            >
              Show {Math.min(PAGE, trades.length - shown)} more of {trades.length - shown}
            </button>
          )}
        </div>
      )}
    </Section>
  );
}

function Details({ report }: { report: BacktestReport }) {
  const o = report.orders;
  const signals = Object.entries(report.signals).sort((a, b) => b[1] - a[1]);
  const tape = report.tape;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Section title="How the simulation works">
        <ul className="flex list-disc flex-col gap-1.5 pl-4 text-sm">
          {report.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      </Section>
      <Section title="Orders">
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
          <dt className="text-muted">Orders placed</dt>
          <dd className="tabular text-right">{formatNumber(o.orders ?? 0)}</dd>
          <dt className="text-muted">Filled</dt>
          <dd className="tabular text-right">{formatNumber(o.filled ?? 0)}</dd>
          <dt className="text-muted">Skipped: opened above the buy zone</dt>
          <dd className="tabular text-right">{formatNumber(o.gapped_above_zone ?? 0)}</dd>
          <dt className="text-muted">Skipped: no free slot</dt>
          <dd className="tabular text-right">{formatNumber(o.no_slot ?? 0)}</dd>
          <dt className="text-muted">Skipped: not enough cash</dt>
          <dd className="tabular text-right">{formatNumber(o.no_cash ?? 0)}</dd>
        </dl>
        <p className="text-xs text-muted">
          An order that doesn&apos;t reach its entry simply expires; the setup can place another the
          next session.
        </p>
      </Section>
      <Section title="What the scan saw">
        <p className="text-sm text-muted">
          {formatNumber(Number(tape.stocks ?? 0))} stocks replayed over{" "}
          {formatNumber(Number(tape.stock_sessions ?? 0))} stock-sessions;{" "}
          {formatNumber(Number(tape.setups ?? 0))} setups waited below a pivot on{" "}
          {formatNumber(Number(tape.candidates ?? 0))} sessions.{" "}
          {tape.reused
            ? "The replay was reused from an earlier run with the same dates and settings."
            : `The replay took ${Math.round(Number(tape.seconds ?? 0))} seconds.`}
        </p>
        <dl className="grid grid-cols-[1fr_auto] gap-x-4 gap-y-1 text-sm">
          {signals.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted">{SIGNAL_NAMES[k] ?? k}</dt>
              <dd className="tabular text-right">{formatNumber(v)}</dd>
            </div>
          ))}
        </dl>
      </Section>
    </div>
  );
}

export function BacktestReportView({ id }: { id: number }) {
  const q = runQuery(id);
  const run = useQuery({
    queryKey: q.key,
    queryFn: () => api.get<BacktestDetail>(q.path),
    refetchInterval: (query) => pollWhileActive(query.state.data),
  });
  if (run.isPending) return <div className="h-96 animate-pulse rounded bg-surface-2" />;
  if (run.error)
    return (
      <p role="alert" className="text-sm text-fall">
        {run.error.message}
      </p>
    );
  const r = run.data;
  const report = r.report;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <Link href="/backtests" className="text-sm text-muted hover:text-foreground">
          ← Backtest lab
        </Link>
        <h1 className="text-xl font-semibold">{r.name}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusLabel run={r} />
          <span>
            {r.start} to {r.end} · ${formatNumber(r.params.portfolio.initial_capital)} ·{" "}
            {r.params.portfolio.risk_pct}% risk · up to {r.params.portfolio.max_positions} positions
            {r.screen ? ` · screen “${r.screen}”` : ""}
          </span>
        </p>
      </div>
      {r.status !== "done" && (
        <p
          role={r.status === "failed" ? "alert" : "status"}
          className={cn("text-sm", r.status === "failed" ? "text-fall" : "text-muted")}
        >
          {progressText(r)}
        </p>
      )}
      {report && (
        <>
          <div
            role="note"
            className="flex flex-col gap-1 rounded-md border border-warn px-3 py-2 text-sm"
          >
            <p>
              <span className="font-semibold text-warn">◆ Hypothetical.</span>{" "}
              {report.labels.hypothetical}
            </p>
            <p>
              <span className="font-semibold text-warn">◆ Survivorship bias.</span>{" "}
              {report.labels.survivorship.replace(/^Survivorship bias: /, "")}
            </p>
          </div>
          <Headline s={report.summary} />
          <Section
            title="Equity"
            note={
              <span className="inline-flex gap-3">
                <span className="inline-flex items-center gap-1">
                  <span aria-hidden className="inline-block h-0.5 w-4 bg-rise" /> Strategy
                </span>
                <span className="inline-flex items-center gap-1">
                  <span aria-hidden className="inline-block h-0.5 w-4 bg-muted" /> SPY, same capital
                </span>
              </span>
            }
          >
            <EquityChart equity={report.equity} split={report.period.split} />
          </Section>
          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="In sample against out of sample">
              <Samples report={report} />
              <p className="text-xs text-muted">
                The same rules over the first {report.samples.split_pct}% of the period and the
                rest. Nothing is fitted on the first part, so a big gap between them points to luck
                or a market that changed.
              </p>
            </Section>
            <Heatmap report={report} />
          </div>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <Breakdown
              title="By market regime at entry"
              rows={report.by_regime}
              label={regimeLabel}
            />
            <Breakdown title="By pattern" rows={report.by_pattern} label={patternLabel} />
            <Breakdown title="By grade" rows={report.by_grade} />
            <Breakdown title="By exit" rows={report.by_exit} />
            <Breakdown title="By year of entry" rows={report.by_year} />
          </div>
          <Trades runId={r.id} trades={r.trades} />
          <Details report={report} />
        </>
      )}
    </div>
  );
}

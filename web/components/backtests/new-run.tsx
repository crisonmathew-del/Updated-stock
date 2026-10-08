"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  api,
  type BacktestExits,
  type BacktestInput,
  type BacktestOptions,
  type BacktestPortfolio,
  type BacktestRun,
  type SetupGrade,
} from "@/lib/api";
import { BACKTESTS, OPTIONS } from "./queries";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";
const GRADES: SetupGrade[] = ["A+", "A", "B", "C"];

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 text-sm">
      <label className="flex flex-col gap-1">
        <span className="text-muted">{label}</span>
        {children}
      </label>
      {hint && <span className="text-xs text-muted">{hint}</span>}
    </div>
  );
}

function Number_({
  value,
  onChange,
  step = "any",
  min = 0,
  className = "w-24",
}: {
  value: number;
  onChange: (v: number) => void;
  step?: string;
  min?: number;
  className?: string;
}) {
  return (
    <input
      type="number"
      inputMode="decimal"
      step={step}
      min={min}
      required
      value={Number.isFinite(value) ? value : ""}
      onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))}
      className={`${FIELD} tabular ${className}`}
    />
  );
}

function Check({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-3 border-t border-border pt-3">
      <legend className="pr-2 text-xs font-semibold tracking-wide text-muted uppercase">
        {title}
      </legend>
      <div className="flex flex-wrap items-start gap-3">{children}</div>
    </fieldset>
  );
}

/** The form, seeded from the settings' defaults (the options call). */
export function RunForm({ options }: { options: BacktestOptions }) {
  const d = options.defaults;
  const router = useRouter();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [start, setStart] = useState(d.start);
  const [end, setEnd] = useState(d.end);
  const [minGrade, setMinGrade] = useState<SetupGrade | "">(d.rules.min_grade ?? "");
  const [patterns, setPatterns] = useState<string[]>(d.rules.patterns);
  const [nearOnly, setNearOnly] = useState(d.rules.near_pivot_only);
  const [skipWide, setSkipWide] = useState(d.rules.skip_risk_too_wide);
  const [skipCorrection, setSkipCorrection] = useState(d.rules.skip_correction);
  const [screen, setScreen] = useState<number | "">("");
  const [portfolio, setPortfolio] = useState<BacktestPortfolio>(d.portfolio);
  const [exits, setExits] = useState<BacktestExits>(d.exits);
  const [sensitivity, setSensitivity] = useState(false);
  const busy = options.running != null;

  const start_ = useMutation({
    mutationFn: (body: BacktestInput) => api.post<BacktestRun>("/api/backtests", body),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: BACKTESTS.key });
      void client.invalidateQueries({ queryKey: OPTIONS.key });
      router.push(`/backtests/${run.id}`);
    },
  });

  const p = (key: keyof BacktestPortfolio) => (v: number) =>
    setPortfolio({ ...portfolio, [key]: v });
  const x =
    <K extends keyof BacktestExits>(key: K) =>
    (v: BacktestExits[K]) =>
      setExits({ ...exits, [key]: v });

  function submit(event: FormEvent) {
    event.preventDefault();
    start_.mutate({
      name: name.trim() || null,
      start,
      end,
      rules: {
        min_grade: minGrade || null,
        patterns,
        near_pivot_only: nearOnly,
        skip_risk_too_wide: skipWide,
        skip_correction: skipCorrection,
      },
      portfolio,
      exits,
      sensitivity,
      screen_id: screen === "" ? null : screen,
    });
  }

  return (
    <form aria-label="New backtest" onSubmit={submit} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Name (optional)">
          <input
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            placeholder="Described from the rules"
            className={`${FIELD} w-64`}
          />
        </Field>
        <Field label="From">
          <input
            type="date"
            required
            value={start}
            min={options.first_date ?? undefined}
            max={end}
            onChange={(e) => setStart(e.target.value)}
            className={`${FIELD} tabular`}
          />
        </Field>
        <Field label="To">
          <input
            type="date"
            required
            value={end}
            min={start}
            max={options.last_date ?? undefined}
            onChange={(e) => setEnd(e.target.value)}
            className={`${FIELD} tabular`}
          />
        </Field>
      </div>

      <Group title="Which setups">
        <Field label="Grade at least">
          <select
            value={minGrade}
            onChange={(e) => setMinGrade(e.target.value as SetupGrade | "")}
            className={FIELD}
          >
            {GRADES.map((g) => (
              <option key={g} value={g}>
                {g}
              </option>
            ))}
            <option value="">Any grade</option>
          </select>
        </Field>
        <Field label="Saved screen" hint="Only setups whose stock matches it that day">
          <select
            value={screen}
            onChange={(e) => setScreen(e.target.value === "" ? "" : Number(e.target.value))}
            className={`${FIELD} w-48`}
          >
            <option value="">None</option>
            {options.screens.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </Field>
        <div className="flex flex-col gap-1.5 pt-5">
          <Check
            label="Near the pivot only (within 3%)"
            checked={nearOnly}
            onChange={setNearOnly}
          />
          <Check
            label="Skip setups whose stop is wider than the maximum"
            checked={skipWide}
            onChange={setSkipWide}
          />
          <Check
            label="No new entries in a market correction"
            checked={skipCorrection}
            onChange={setSkipCorrection}
          />
        </div>
        <fieldset className="flex flex-col gap-1">
          <legend className="text-sm text-muted">Patterns (none ticked: all)</legend>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {options.patterns.map((o) => (
              <Check
                key={o.value}
                label={o.label}
                checked={patterns.includes(o.value)}
                onChange={(on) =>
                  setPatterns(on ? [...patterns, o.value] : patterns.filter((v) => v !== o.value))
                }
              />
            ))}
          </div>
        </fieldset>
      </Group>

      <Group title="Portfolio and costs">
        <Field label="Starting capital ($)">
          <Number_
            value={portfolio.initial_capital}
            onChange={p("initial_capital")}
            className="w-28"
          />
        </Field>
        <Field label="Risk per trade (%)">
          <Number_ value={portfolio.risk_pct} onChange={p("risk_pct")} />
        </Field>
        <Field label="Largest position (%)">
          <Number_ value={portfolio.max_position_pct} onChange={p("max_position_pct")} />
        </Field>
        <Field label="Positions at once">
          <Number_ value={portfolio.max_positions} onChange={p("max_positions")} step="1" min={1} />
        </Field>
        <Field label="Slippage (%)">
          <Number_ value={portfolio.slippage_pct} onChange={p("slippage_pct")} />
        </Field>
        <Field label="Commission per order ($)">
          <Number_ value={portfolio.commission} onChange={p("commission")} />
        </Field>
      </Group>

      <Group title="Exits">
        <Field label="Trailing exit">
          <select
            value={exits.trailing}
            onChange={(e) => x("trailing")(e.target.value as BacktestExits["trailing"])}
            className={FIELD}
          >
            <option value="sma50">Close below the 50-day SMA</option>
            <option value="ema21">Close below the 21-day EMA</option>
            <option value="none">None</option>
          </select>
        </Field>
        <Field label="Time stop after (sessions)" hint="0 switches it off">
          <Number_ value={exits.time_stop_sessions} onChange={x("time_stop_sessions")} step="1" />
        </Field>
        <Field label="…unless up at least (%)">
          <Number_ value={exits.time_stop_min_gain_pct} onChange={x("time_stop_min_gain_pct")} />
        </Field>
        <Field label="Partial profit at (+%)">
          <Number_ value={exits.partial_profit_pct} onChange={x("partial_profit_pct")} />
        </Field>
        <Field label="Selling (% of shares)" hint="0 switches it off">
          <Number_ value={exits.partial_fraction_pct} onChange={x("partial_fraction_pct")} />
        </Field>
        <Field label="Breakeven stop at (R)">
          <Number_ value={exits.breakeven_r} onChange={x("breakeven_r")} />
        </Field>
        <Field label="…or at (+%)">
          <Number_ value={exits.breakeven_gain_pct} onChange={x("breakeven_gain_pct")} />
        </Field>
        <div className="pt-5">
          <Check
            label="Sell at the close when the breakout isn't confirmed"
            checked={exits.sell_unconfirmed}
            onChange={x("sell_unconfirmed")}
          />
        </div>
      </Group>

      <Group title="Robustness">
        <Check
          label={`Sensitivity heatmap: VCP final contraction ${options.grid.vcp.join(", ")}% × breakout volume ${options.grid.volume.join(", ")}%`}
          checked={sensitivity}
          onChange={setSensitivity}
        />
        <p className="text-xs text-muted">
          The first heatmap for a period replays the scan once per combination: it takes several
          times longer than a single run. Later runs reuse it.
        </p>
      </Group>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" disabled={busy || start_.isPending}>
          Run backtest
        </Button>
        {busy && (
          <span className="text-sm text-muted">
            A backtest is running; start the next one when it finishes.
          </span>
        )}
        {start_.error && (
          <span role="alert" className="text-sm text-fall">
            {start_.error.message}
          </span>
        )}
      </div>
    </form>
  );
}

export function NewRun() {
  const options = useQuery({
    queryKey: OPTIONS.key,
    queryFn: () => api.get<BacktestOptions>(OPTIONS.path),
  });
  if (options.isPending) return <div className="h-64 animate-pulse rounded bg-surface-2" />;
  if (options.error)
    return (
      <p role="alert" className="text-sm text-fall">
        {options.error.message}
      </p>
    );
  if (!options.data.last_date)
    return (
      <p className="text-sm text-muted">
        No price history analysed yet. Come back once prices have loaded and the evening scan has
        run (progress on Admin → Data; by hand: make backfill, then make scan-now).
      </p>
    );
  return <RunForm options={options.data} />;
}

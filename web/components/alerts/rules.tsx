"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { RULES } from "@/components/alerts/queries";
import { Button } from "@/components/ui/button";
import {
  api,
  type AlertChannel,
  type AlertPriority,
  type AlertRule,
  type AlertRuleInput,
  type MovingAverage,
  type RuleCondition,
  type RuleScope,
  type SavedScreen,
  type Watchlist,
} from "@/lib/api";
import { formatAgo } from "@/lib/format";
import { useToasts } from "@/stores/toast";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";

export const SCOPES: { value: RuleScope; label: string }[] = [
  { value: "ticker", label: "A stock" },
  { value: "watchlist", label: "Any stock in a watchlist" },
  { value: "holdings", label: "Any of my holdings" },
  { value: "screen", label: "A saved screen" },
];

export const CONDITIONS: {
  value: RuleCondition;
  label: string;
  /** What the value box asks for (none: no value). */
  unit?: string;
  scopes: RuleScope[];
}[] = [
  { value: "price_above", label: "Trades above a price", unit: "Price", scopes: ["ticker"] },
  { value: "price_below", label: "Trades below a price", unit: "Price", scopes: ["ticker"] },
  {
    value: "ma_cross_above",
    label: "Crosses above a moving average",
    scopes: ["ticker", "watchlist", "holdings"],
  },
  {
    value: "ma_cross_below",
    label: "Crosses below a moving average",
    scopes: ["ticker", "watchlist", "holdings"],
  },
  {
    value: "change_above",
    label: "Is up on the day by at least",
    unit: "% up",
    scopes: ["ticker", "watchlist", "holdings"],
  },
  {
    value: "change_below",
    label: "Is down on the day by at least",
    unit: "% down",
    scopes: ["ticker", "watchlist", "holdings"],
  },
  {
    value: "volume_ratio_above",
    label: "Projects volume of at least",
    unit: "× average",
    scopes: ["ticker", "watchlist", "holdings"],
  },
  { value: "new_match", label: "Newly matches it (after the close)", scopes: ["screen"] },
];

const AVERAGES: { value: MovingAverage; label: string }[] = [
  { value: "ema10", label: "10-day EMA" },
  { value: "ema21", label: "21-day EMA" },
  { value: "sma50", label: "50-day SMA" },
  { value: "sma150", label: "150-day SMA" },
  { value: "sma200", label: "200-day SMA" },
];

export type Draft = {
  scope: RuleScope;
  symbol: string;
  watchlistId: string;
  screenId: string;
  condition: RuleCondition;
  value: string;
  ma: MovingAverage;
  inApp: boolean;
  email: boolean;
  priority: AlertPriority;
  name: string;
};

/** A name when none is given: "SPOT above 95", "Leaders: below 50-day SMA". */
export function suggestName(
  d: Draft,
  watchlists: Watchlist[] = [],
  screens: SavedScreen[] = [],
): string {
  const target =
    d.scope === "ticker"
      ? d.symbol.toUpperCase() || "Stock"
      : d.scope === "watchlist"
        ? (watchlists.find((w) => String(w.id) === d.watchlistId)?.name ?? "Watchlist")
        : d.scope === "holdings"
          ? "Holdings"
          : (screens.find((s) => String(s.id) === d.screenId)?.name ?? "Screen");
  const ma = AVERAGES.find((a) => a.value === d.ma)?.label ?? "";
  const what: Record<RuleCondition, string> = {
    price_above: `above ${d.value}`,
    price_below: `below ${d.value}`,
    ma_cross_above: `above ${ma}`,
    ma_cross_below: `below ${ma}`,
    change_above: `up ${d.value}%`,
    change_below: `down ${d.value}%`,
    volume_ratio_above: `volume ${d.value}×`,
    new_match: "new matches",
  };
  const sep = d.scope === "ticker" ? " " : ": ";
  return `${target}${sep}${what[d.condition]}`.slice(0, 80);
}

export function ruleInput(
  d: Draft,
  watchlists: Watchlist[] = [],
  screens: SavedScreen[] = [],
): AlertRuleInput {
  const value = d.value === "" ? null : Number(d.value);
  const channels: AlertChannel[] = [
    ...(d.inApp ? (["in_app"] as const) : []),
    ...(d.email ? (["email"] as const) : []),
  ];
  return {
    name: d.name.trim() || suggestName(d, watchlists, screens),
    scope: d.scope,
    symbol: d.scope === "ticker" ? d.symbol.trim().toUpperCase() : null,
    watchlist_id: d.scope === "watchlist" && d.watchlistId ? Number(d.watchlistId) : null,
    screen_id: d.scope === "screen" && d.screenId ? Number(d.screenId) : null,
    condition: d.condition,
    // "Down 4%" is entered as 4 and stored as −4.
    value: value != null && d.condition === "change_below" ? -Math.abs(value) : value,
    ma: d.condition.startsWith("ma_cross") ? d.ma : null,
    channels,
    priority: d.priority,
  };
}

function RuleForm({ initialSymbol, onCreated }: { initialSymbol: string; onCreated: () => void }) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const watchlists = useQuery({
    queryKey: ["watchlists"],
    queryFn: () => api.get<Watchlist[]>("/api/watchlists"),
  });
  const screens = useQuery({
    queryKey: ["screens"],
    queryFn: () => api.get<SavedScreen[]>("/api/screens"),
  });
  const [d, setD] = useState<Draft>({
    scope: "ticker",
    symbol: initialSymbol,
    watchlistId: "",
    screenId: "",
    condition: "price_above",
    value: "",
    ma: "sma50",
    inApp: true,
    email: true,
    priority: "high",
    name: "",
  });
  const set = (change: Partial<Draft>) => setD((old) => ({ ...old, ...change }));
  const condition = CONDITIONS.find((c) => c.value === d.condition);
  const create = useMutation({
    mutationFn: () =>
      api.post<AlertRule>("/api/alert-rules", ruleInput(d, watchlists.data, screens.data)),
    onSuccess: (rule) => {
      void client.invalidateQueries({ queryKey: RULES.key });
      push(`Alert rule "${rule.name}" is on.`);
      set({ value: "", name: "" });
      onCreated();
    },
  });

  function changeScope(scope: RuleScope) {
    const fits = CONDITIONS.find((c) => c.value === d.condition)?.scopes.includes(scope);
    const first = CONDITIONS.find((c) => c.scopes.includes(scope));
    set({ scope, condition: fits ? d.condition : (first?.value ?? "new_match") });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    create.mutate();
  }

  return (
    <form
      onSubmit={submit}
      aria-label="New alert rule"
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4"
    >
      <h2 className="text-sm font-semibold">New alert rule</h2>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">When</span>
          <select
            value={d.scope}
            onChange={(e) => changeScope(e.target.value as RuleScope)}
            className={FIELD}
          >
            {SCOPES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        {d.scope === "ticker" && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">Stock</span>
            <input
              value={d.symbol}
              onChange={(e) => set({ symbol: e.target.value.toUpperCase() })}
              placeholder="e.g. NVDA"
              maxLength={16}
              required
              className={`${FIELD} w-28 uppercase`}
            />
          </label>
        )}
        {d.scope === "watchlist" && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">Watchlist</span>
            <select
              value={d.watchlistId}
              onChange={(e) => set({ watchlistId: e.target.value })}
              required
              className={FIELD}
            >
              <option value="">Choose…</option>
              {watchlists.data?.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {d.scope === "screen" && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">Saved screen</span>
            <select
              value={d.screenId}
              onChange={(e) => set({ screenId: e.target.value })}
              required
              className={FIELD}
            >
              <option value="">Choose…</option>
              {screens.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Condition</span>
          <select
            value={d.condition}
            onChange={(e) => set({ condition: e.target.value as RuleCondition })}
            className={FIELD}
          >
            {CONDITIONS.filter((c) => c.scopes.includes(d.scope)).map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        {condition?.unit && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">{condition.unit}</span>
            <input
              type="number"
              inputMode="decimal"
              step="any"
              min="0"
              value={d.value}
              onChange={(e) => set({ value: e.target.value })}
              required
              className={`${FIELD} tabular w-24`}
            />
          </label>
        )}
        {d.condition.startsWith("ma_cross") && (
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-muted">Moving average</span>
            <select
              value={d.ma}
              onChange={(e) => set({ ma: e.target.value as MovingAverage })}
              className={FIELD}
            >
              {AVERAGES.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        <fieldset className="flex items-center gap-3 text-sm">
          <legend className="sr-only">Channels</legend>
          <span className="text-muted">Notify</span>
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={d.inApp}
              onChange={(e) => set({ inApp: e.target.checked })}
            />
            In-app
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={d.email}
              onChange={(e) => set({ email: e.target.checked })}
            />
            Email
          </label>
        </fieldset>
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted">Priority</span>
          <select
            value={d.priority}
            onChange={(e) => set({ priority: e.target.value as AlertPriority })}
            className={FIELD}
          >
            <option value="high">High (email now)</option>
            <option value="normal">Normal (digest)</option>
          </select>
        </label>
        <label className="flex min-w-48 flex-1 flex-col gap-1 text-sm">
          <span className="text-muted">Name (optional)</span>
          <input
            value={d.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder={suggestName(d, watchlists.data, screens.data)}
            maxLength={80}
            className={FIELD}
          />
        </label>
        <Button
          type="submit"
          variant="primary"
          disabled={create.isPending || (!d.inApp && !d.email)}
        >
          Create rule
        </Button>
      </div>
      {!d.inApp && !d.email && <p className="text-xs text-muted">Choose at least one channel.</p>}
      {create.isError && (
        <p role="alert" className="text-sm text-fall">
          {create.error.message}
        </p>
      )}
    </form>
  );
}

function RuleRow({ rule }: { rule: AlertRule }) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [confirming, setConfirming] = useState(false);
  const refresh = () => void client.invalidateQueries({ queryKey: RULES.key });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) =>
      api.patch<AlertRule>(`/api/alert-rules/${rule.id}`, { enabled }),
    onSuccess: refresh,
    onError: (error) => push(`Couldn't change the rule: ${error.message}`, "error"),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/alert-rules/${rule.id}`),
    onSuccess: () => {
      refresh();
      push(`Deleted "${rule.name}".`);
    },
    onError: (error) => push(`Couldn't delete the rule: ${error.message}`, "error"),
  });
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border py-3 last:border-0">
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={rule.enabled}
          disabled={toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
          aria-label={`${rule.enabled ? "Turn off" : "Turn on"} ${rule.name}`}
        />
      </label>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">
          {rule.name}
          {!rule.enabled && <span className="ml-2 text-xs font-normal text-muted">(off)</span>}
        </p>
        <p className="text-sm text-muted">{rule.description}</p>
        <p className="text-xs text-muted">
          {rule.last_fired_at ? `Last fired ${formatAgo(rule.last_fired_at)}` : "Hasn't fired yet"}
        </p>
      </div>
      {confirming ? (
        <span className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Keep it
          </Button>
          <Button size="sm" className="text-fall" onClick={() => remove.mutate()}>
            Delete rule
          </Button>
        </span>
      ) : (
        <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
          Delete
        </Button>
      )}
    </li>
  );
}

/** Your alert rules and the builder for new ones. */
export function AlertRules({ initialSymbol = "" }: { initialSymbol?: string }) {
  const rules = useQuery({ queryKey: RULES.key, queryFn: () => api.get<AlertRule[]>(RULES.path) });
  return (
    <div className="flex flex-col gap-4">
      <RuleForm initialSymbol={initialSymbol} onCreated={() => undefined} />
      <section aria-labelledby="rules-title">
        <h2
          id="rules-title"
          className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase"
        >
          Your rules
        </h2>
        {rules.isSuccess && rules.data.length === 0 && (
          <p className="py-4 text-sm text-muted">
            No rules yet. Setup alerts (breakouts, stops, near pivot) need no rule: they&apos;re on
            for A-grade setups and anything you hold or watch.
          </p>
        )}
        <ul>
          {rules.data?.map((r) => (
            <RuleRow key={r.id} rule={r} />
          ))}
        </ul>
      </section>
    </div>
  );
}

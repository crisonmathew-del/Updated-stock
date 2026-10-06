"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DataHealthPanel } from "@/components/admin/data-health-panel";
import { Section, StatusMark } from "@/components/ui/section";
import { Button } from "@/components/ui/button";
import { api, type ProvidersStatus, type SettingItem } from "@/lib/api";
import { humanize } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useToasts } from "@/stores/toast";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";
const SETTINGS = { key: ["settings"], path: "/api/settings" } as const;
const KEYS = { key: ["settings", "keys"], path: "/api/settings/keys" } as const;

/** Sections in the order a trader thinks about them (spec §8.10). */
export const CATEGORIES: { key: string; label: string; note: string }[] = [
  {
    key: "risk",
    label: "Account and risk",
    note: "Account size, risk per trade, the plan's entry and stop rules",
  },
  {
    key: "universe",
    label: "Universe",
    note: "Which stocks are scanned: price, liquidity, market cap",
  },
  { key: "trend", label: "Trend and stage", note: "The Trend Template and Weinstein stages" },
  {
    key: "patterns",
    label: "Patterns",
    note: "Base detection: VCP, flat base, cup, high tight flag…",
  },
  {
    key: "entries",
    label: "Entries and breakouts",
    note: "Breakout volume, buy zone, gaps, pocket pivots",
  },
  { key: "scoring", label: "Setup score", note: "Weights, grades and red-flag penalties" },
  { key: "fundamentals", label: "Fundamentals", note: "The Fundamentals Grade" },
  { key: "groups", label: "Industry groups", note: "Group ranking" },
  { key: "market", label: "Market regime", note: "Distribution days and follow-through" },
  { key: "intraday", label: "Intraday", note: "The live watcher and scans" },
  { key: "alerts", label: "Alerts", note: "Who gets what, when, and the digests" },
  { key: "backtest", label: "Backtest lab", note: "The lab's defaults and the sensitivity grid" },
  { key: "data", label: "Data", note: "History and insider data loading" },
];

type Draft = Record<string, unknown>;

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function show(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object")
    return Object.entries(value)
      .map(([k, v]) => `${humanize(k)} ${String(v)}`)
      .join(" · ");
  if (typeof value === "boolean") return value ? "On" : "Off";
  if (value === "" || value == null) return "(empty)";
  return String(value);
}

function bounds(item: SettingItem): string {
  const c = item.constraints;
  const low = c.minimum ?? c.exclusiveMinimum;
  const high = c.maximum ?? c.exclusiveMaximum;
  if (low == null && high == null) return "";
  if (low != null && high != null) return `${low}–${high}`;
  return low != null ? `from ${low}` : `up to ${high}`;
}

function Editor({
  item,
  value,
  onChange,
}: {
  item: SettingItem;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const c = item.constraints;
  const id = `setting-${item.key}`;
  if (c.enum) {
    return (
      <select
        id={id}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
        className={FIELD}
      >
        {c.enum.map((o) => (
          <option key={String(o)} value={String(o)}>
            {String(o)}
          </option>
        ))}
      </select>
    );
  }
  if (c.type === "boolean") {
    return (
      <input
        id={id}
        type="checkbox"
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  if (c.type === "integer" || c.type === "number") {
    return (
      <input
        id={id}
        type="number"
        inputMode="decimal"
        step={c.type === "integer" ? 1 : "any"}
        min={c.minimum ?? c.exclusiveMinimum}
        max={c.maximum ?? c.exclusiveMaximum}
        value={value == null || Number.isNaN(value) ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        className={`${FIELD} tabular w-28`}
      />
    );
  }
  if (c.type === "array") {
    return (
      <input
        id={id}
        value={Array.isArray(value) ? value.join(", ") : String(value ?? "")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean)
              .map(Number),
          )
        }
        className={`${FIELD} tabular w-56`}
      />
    );
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, number>;
    return (
      <fieldset id={id} className="grid grid-cols-[auto_auto] items-center gap-x-2 gap-y-1">
        <legend className="sr-only">{item.description}</legend>
        {Object.entries(record).map(([k, v]) => (
          <label key={k} className="contents text-xs">
            <span className="text-muted">{humanize(k)}</span>
            <input
              type="number"
              step="any"
              value={Number.isNaN(v) ? "" : v}
              onChange={(e) => onChange({ ...record, [k]: Number(e.target.value) })}
              className={`${FIELD} tabular h-7 w-20`}
            />
          </label>
        ))}
      </fieldset>
    );
  }
  return (
    <input
      id={id}
      value={String(value ?? "")}
      pattern={c.pattern}
      onChange={(e) => onChange(e.target.value)}
      className={`${FIELD} w-40`}
    />
  );
}

function CategorySection({
  category,
  items,
  label,
  note,
}: {
  category: string;
  items: SettingItem[];
  label: string;
  note: string;
}) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [draft, setDraft] = useState<Draft>({});
  const [confirm, setConfirm] = useState(false);
  const changed = Object.keys(draft).filter((k) => {
    const item = items.find((i) => i.key === k);
    return item && !same(draft[k], item.value);
  });
  const save = useMutation({
    mutationFn: () =>
      api.patch<{ items: SettingItem[] }>(SETTINGS.path, {
        changes: Object.fromEntries(changed.map((k) => [k, draft[k]])),
      }),
    onSuccess: (data) => {
      client.setQueryData(SETTINGS.key, data);
      push(
        `Saved ${changed.length} ${label.toLowerCase()} setting${changed.length === 1 ? "" : "s"}.`,
      );
      setDraft({});
    },
  });
  const reset = useMutation({
    mutationFn: (keys: string[]) =>
      api.post<{ items: SettingItem[] }>(`${SETTINGS.path}/reset`, { keys }),
    onSuccess: (data, keys) => {
      client.setQueryData(SETTINGS.key, data);
      setDraft((d) => Object.fromEntries(Object.entries(d).filter(([k]) => !keys.includes(k))));
      setConfirm(false);
      push(keys.length === 1 ? "Back to the default." : `${label}: back to the defaults.`);
    },
  });
  const modified = items.filter((i) => !same(i.value, i.default));
  return (
    <Section title={label} note={note} anchor={`settings-${category}`}>
      <ul className="flex flex-col divide-y divide-border">
        {items.map((item) => {
          const value = item.key in draft ? draft[item.key] : item.value;
          const isDefault = same(item.value, item.default);
          return (
            <li key={item.key} className="flex flex-wrap items-start gap-x-4 gap-y-1 py-2">
              <div className="min-w-0 flex-1 basis-64 text-sm">
                <label htmlFor={`setting-${item.key}`}>{item.description}</label>
                <span className="block text-xs text-muted">
                  {isDefault ? "Default" : `Default: ${show(item.default)}`}
                  {bounds(item) ? ` · ${bounds(item)}` : ""}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Editor
                  item={item}
                  value={value}
                  onChange={(v) => setDraft({ ...draft, [item.key]: v })}
                />
                {!isDefault && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Reset “${item.description}” to its default`}
                    onClick={() => reset.mutate([item.key])}
                  >
                    Reset
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          size="sm"
          disabled={changed.length === 0 || save.isPending}
          onClick={() => save.mutate()}
        >
          {changed.length
            ? `Save ${changed.length} change${changed.length === 1 ? "" : "s"}`
            : "No changes"}
        </Button>
        {changed.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => setDraft({})}>
            Discard
          </Button>
        )}
        {modified.length > 0 &&
          (confirm ? (
            <Button size="sm" onClick={() => reset.mutate(items.map((i) => i.key))}>
              Confirm: reset {modified.length} to the defaults
            </Button>
          ) : (
            <Button size="sm" variant="ghost" onClick={() => setConfirm(true)}>
              Reset section to defaults
            </Button>
          ))}
        {(save.error || reset.error) && (
          <span role="alert" className="text-sm text-fall">
            {(save.error ?? reset.error)?.message}
          </span>
        )}
      </div>
    </Section>
  );
}

function KeysPanel() {
  const keys = useQuery({
    queryKey: KEYS.key,
    queryFn: () => api.get<ProvidersStatus>(KEYS.path),
  });
  if (!keys.data) return null;
  const p = keys.data.providers;
  return (
    <Section
      title="Data sources and keys"
      note="Keys live in .env on the server; this page only says whether each is set"
    >
      <p className="text-sm text-muted">
        Prices: {p.prices === "yfinance" ? "yfinance (development only)" : p.prices} · Real-time:{" "}
        {p.stream} · Fundamentals: {p.fundamentals} · News: {p.news} · Email: {p.email}
      </p>
      <table className="w-full text-sm">
        <caption className="sr-only">Each service, whether its key is set and what it does</caption>
        <tbody>
          {keys.data.keys.map((k) => (
            <tr key={k.name} className="border-t border-border align-top">
              <th scope="row" className="py-1.5 pr-3 text-left font-normal whitespace-nowrap">
                <span className="inline-flex items-center gap-1.5">
                  <StatusMark status={k.configured ? "pass" : k.in_use ? "fail" : "no_data"} />
                  {k.name}
                </span>
              </th>
              <td className="py-1.5 pr-3">
                {k.configured ? "Configured" : k.in_use ? "Missing (selected)" : "Not set"}
                {k.configured && !k.in_use ? " · not selected" : ""}
              </td>
              <td className="py-1.5 pr-3 text-muted">{k.purpose}</td>
              <td className="py-1.5 text-xs text-muted">{k.env.join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  );
}

export function SettingsPage() {
  const settings = useQuery({
    queryKey: SETTINGS.key,
    queryFn: () => api.get<{ items: SettingItem[] }>(SETTINGS.path),
  });
  const [open, setOpen] = useState<string>("risk");
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted">
          Every threshold the scan, the scores, the alerts and the backtest lab use. Changes apply
          from the next scan; a change to trend or stage settings needs a full rescan (make scan-now
          full=1).
        </p>
      </div>
      <KeysPanel />
      {settings.error && (
        <p role="alert" className="text-sm text-fall">
          {settings.error.message}
        </p>
      )}
      {settings.data && (
        <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
          <nav aria-label="Setting sections" className="flex flex-col gap-0.5 text-sm">
            {CATEGORIES.map((c) => {
              const count = settings.data.items.filter((i) => i.category === c.key).length;
              if (!count) return null;
              return (
                <button
                  key={c.key}
                  type="button"
                  aria-current={open === c.key ? "true" : undefined}
                  onClick={() => setOpen(c.key)}
                  className={cn(
                    "rounded-md px-2 py-1 text-left",
                    open === c.key
                      ? "bg-surface-2 text-foreground"
                      : "text-muted hover:text-foreground",
                  )}
                >
                  {c.label} <span className="text-xs text-muted">({count})</span>
                </button>
              );
            })}
          </nav>
          {CATEGORIES.filter((c) => c.key === open).map((c) => (
            <CategorySection
              key={c.key}
              category={c.key}
              label={c.label}
              note={c.note}
              items={settings.data.items.filter((i) => i.category === c.key)}
            />
          ))}
        </div>
      )}
      <DataHealthPanel />
    </div>
  );
}

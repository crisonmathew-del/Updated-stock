"use client";

import * as Popover from "@radix-ui/react-popover";
import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import type { ScreenFilter } from "@/lib/api";
import { describeFilter, type Field, FIELD_GROUPS, FIELDS, newFilter } from "@/lib/screener";
import { cn } from "@/lib/utils";

const POPOVER =
  "z-30 flex max-h-[min(28rem,var(--radix-popover-content-available-height))] w-72 flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-surface p-3 text-sm shadow-lg";

/** Fields a filter can use: everything except the symbol, name and sparkline. */
export const FILTERABLE = (Object.keys(FIELDS) as Field[]).filter(
  (f) => !["symbol", "name", "spark"].includes(f),
);

function parse(text: string): number | null {
  const n = Number.parseFloat(text.replace("−", "-"));
  return Number.isFinite(n) ? n : null;
}

function RangeEditor({
  filter,
  onChange,
}: {
  filter: Extract<ScreenFilter, { op: "between" }>;
  onChange: (f: ScreenFilter) => void;
}) {
  const id = useId();
  const unit = FIELDS[filter.field as Field]?.unit;
  const [min, setMin] = useState(filter.min == null ? "" : String(filter.min));
  const [max, setMax] = useState(filter.max == null ? "" : String(filter.max));
  return (
    <div className="grid grid-cols-2 gap-2">
      {(
        [
          ["Min", min, setMin, "min"],
          ["Max", max, setMax, "max"],
        ] as const
      ).map(([label, value, set, key]) => (
        <label
          key={key}
          htmlFor={`${id}-${key}`}
          className="flex flex-col gap-1 text-xs text-muted"
        >
          {label}
          {unit ? ` (${unit})` : ""}
          <input
            id={`${id}-${key}`}
            inputMode="decimal"
            value={value}
            placeholder="any"
            onChange={(event) => {
              set(event.target.value);
              onChange({ ...filter, [key]: parse(event.target.value) });
            }}
            className="tabular h-8 rounded-md border border-border bg-background px-2 text-sm text-foreground"
          />
        </label>
      ))}
    </div>
  );
}

function BoolEditor({
  filter,
  onChange,
}: {
  filter: Extract<ScreenFilter, { op: "is" }>;
  onChange: (f: ScreenFilter) => void;
}) {
  return (
    <div role="radiogroup" aria-label={FIELDS[filter.field as Field]?.label} className="flex gap-2">
      {[true, false].map((value) => (
        <button
          key={String(value)}
          type="button"
          role="radio"
          aria-checked={filter.value === value}
          onClick={() => onChange({ ...filter, value })}
          className={cn(
            "flex-1 rounded-md border px-3 py-1.5",
            filter.value === value ? "border-tide text-foreground" : "border-border text-muted",
          )}
        >
          {value ? "Yes" : "No"}
        </button>
      ))}
    </div>
  );
}

function ListEditor({
  filter,
  options,
  onChange,
}: {
  filter: Extract<ScreenFilter, { op: "in" }>;
  options: { value: string; label: string }[];
  onChange: (f: ScreenFilter) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = query
    ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
    : options;
  const toggle = (value: string) =>
    onChange({
      ...filter,
      values: filter.values.includes(value)
        ? filter.values.filter((v) => v !== value)
        : [...filter.values, value],
    });
  return (
    <div className="flex flex-col gap-2">
      {options.length > 10 && (
        <input
          aria-label="Find an option"
          placeholder="Find…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="h-8 rounded-md border border-border bg-background px-2 text-sm"
        />
      )}
      {options.length === 0 && (
        <p className="text-xs text-muted">No values in today&apos;s data.</p>
      )}
      <ul className="flex flex-col">
        {shown.map((o) => (
          <li key={o.value}>
            <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-1 hover:bg-surface-2">
              <input
                type="checkbox"
                checked={filter.values.includes(o.value)}
                onChange={() => toggle(o.value)}
                className="accent-[var(--tide)]"
              />
              {o.label}
            </label>
          </li>
        ))}
      </ul>
      {filter.values.length > 0 && (
        <button
          type="button"
          onClick={() => onChange({ ...filter, values: [] })}
          className="self-start text-xs text-muted hover:text-foreground"
        >
          Clear
        </button>
      )}
    </div>
  );
}

function FilterChip({
  filter,
  open,
  onOpenChange,
  options,
  onChange,
  onRemove,
}: {
  filter: ScreenFilter;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  options: { value: string; label: string }[];
  onChange: (f: ScreenFilter) => void;
  onRemove: () => void;
}) {
  const def = FIELDS[filter.field as Field];
  const text = describeFilter(filter);
  return (
    <li className="flex items-center rounded-md border border-border bg-surface text-sm">
      <Popover.Root open={open} onOpenChange={onOpenChange}>
        <Popover.Trigger asChild>
          <button
            type="button"
            className="rounded-l-md py-1 pr-1 pl-2.5 hover:bg-surface-2"
            title={`${def?.description ?? ""} Click to edit.`}
          >
            {text}
          </button>
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content align="start" sideOffset={6} className={POPOVER}>
            <div>
              <p className="font-medium">{def?.label ?? filter.field}</p>
              {def && <p className="text-xs text-muted">{def.description}</p>}
            </div>
            {filter.op === "between" && <RangeEditor filter={filter} onChange={onChange} />}
            {filter.op === "is" && <BoolEditor filter={filter} onChange={onChange} />}
            {filter.op === "in" && (
              <ListEditor filter={filter} options={options} onChange={onChange} />
            )}
            <div className="flex justify-between border-t border-border pt-2">
              <Button size="sm" variant="ghost" onClick={onRemove}>
                Remove filter
              </Button>
              <Popover.Close asChild>
                <Button size="sm">Done</Button>
              </Popover.Close>
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      <button
        type="button"
        aria-label={`Remove filter ${text}`}
        onClick={onRemove}
        className="min-w-7 rounded-r-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-foreground"
      >
        ×
      </button>
    </li>
  );
}

function AddFilter({ used, onAdd }: { used: Set<string>; onAdd: (field: Field) => void }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const q = query.toLowerCase();
  const fields = FILTERABLE.filter(
    (f) =>
      !used.has(f) &&
      (!q ||
        FIELDS[f].label.toLowerCase().includes(q) ||
        FIELDS[f].description.toLowerCase().includes(q)),
  );
  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery("");
      }}
    >
      <Popover.Trigger asChild>
        <Button size="sm" variant="ghost" className="border border-dashed border-border">
          ＋ Add filter
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          className={POPOVER}
          // The chosen field's editor opens next and takes focus; returning it to this
          // trigger would count as a click outside that editor and close it.
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <input
            autoFocus
            aria-label="Find a field"
            placeholder="Find a field…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-8 rounded-md border border-border bg-background px-2 text-sm"
          />
          {FIELD_GROUPS.map((group) => {
            const inGroup = fields.filter((f) => FIELDS[f].group === group);
            if (inGroup.length === 0) return null;
            return (
              <div key={group}>
                <p className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">
                  {group}
                </p>
                <ul>
                  {inGroup.map((f) => (
                    <li key={f}>
                      <button
                        type="button"
                        title={FIELDS[f].description}
                        onClick={() => {
                          setOpen(false);
                          setQuery("");
                          onAdd(f);
                        }}
                        className="w-full rounded px-1.5 py-1 text-left hover:bg-surface-2"
                      >
                        {FIELDS[f].label}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
          {fields.length === 0 && <p className="text-xs text-muted">No more fields match.</p>}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/**
 * The custom screen builder: each filter is a chip ("RS Rating ≥ 85") that opens an editor
 * (a range, yes/no, or a list of choices); "Add filter" offers every computed field by group.
 */
export function FilterBar({
  filters,
  onChange,
  optionsFor,
}: {
  filters: ScreenFilter[];
  onChange: (filters: ScreenFilter[]) => void;
  optionsFor: (field: Field) => { value: string; label: string }[];
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const used = new Set(filters.map((f) => f.field));
  return (
    <ul aria-label="Filters" className="flex flex-wrap items-center gap-2">
      {filters.map((filter, i) => (
        <FilterChip
          key={filter.field}
          filter={filter}
          open={editing === filter.field}
          onOpenChange={(open) => setEditing(open ? filter.field : null)}
          options={optionsFor(filter.field as Field)}
          onChange={(next) => onChange(filters.map((f, j) => (j === i ? next : f)))}
          onRemove={() => {
            setEditing(null);
            onChange(filters.filter((_, j) => j !== i));
          }}
        />
      ))}
      <li>
        <AddFilter
          used={used}
          onAdd={(field) => {
            onChange([...filters, newFilter(field)]);
            // A yes/no filter is complete as added; the others open their editor.
            if (FIELDS[field].kind !== "bool") setEditing(field);
          }}
        />
      </li>
    </ul>
  );
}

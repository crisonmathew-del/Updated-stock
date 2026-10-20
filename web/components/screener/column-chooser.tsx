"use client";

import * as Popover from "@radix-ui/react-popover";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { GLOSSARY, type GlossaryKey } from "@/lib/glossary";
import { DEFAULT_COLUMNS, type Field, FIELD_GROUPS, FIELDS } from "@/lib/screener";

const CHOOSABLE = (Object.keys(FIELDS) as Field[]).filter((f) => f !== "symbol" && f !== "name");

/** Pick the result columns (the symbol always shows); order follows the field catalogue. */
export function ColumnChooser({
  columns,
  onChange,
}: {
  columns: Field[];
  onChange: (columns: Field[]) => void;
}) {
  const shown = new Set(columns);
  const toggle = (field: Field) => {
    const next = new Set(shown);
    if (next.has(field)) next.delete(field);
    else next.add(field);
    // Keep the catalogue's order so columns don't jump around as they're toggled.
    const ordered = [...DEFAULT_COLUMNS, ...CHOOSABLE].filter(
      (f, i, all) => all.indexOf(f) === i && (f === "symbol" || next.has(f)),
    );
    onChange(ordered);
  };
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button size="sm">Columns</Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          className="z-30 grid max-h-[min(32rem,var(--radix-popover-content-available-height))] w-[min(36rem,95vw)] grid-cols-2 gap-x-4 gap-y-3 overflow-y-auto rounded-lg border border-border bg-surface p-3 text-sm shadow-lg"
        >
          {FIELD_GROUPS.map((group) => {
            const fields = CHOOSABLE.filter((f) => FIELDS[f].group === group);
            if (fields.length === 0) return null;
            return (
              <fieldset key={group}>
                <legend className="mb-1 text-xs font-semibold tracking-wide text-muted uppercase">
                  {group}
                </legend>
                {fields.map((f) => (
                  <label
                    key={f}
                    title={FIELDS[f].description}
                    className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-surface-2"
                  >
                    <input
                      type="checkbox"
                      checked={shown.has(f)}
                      onChange={() => toggle(f)}
                      className="accent-[var(--tide)]"
                    />
                    {FIELDS[f].label}
                  </label>
                ))}
              </fieldset>
            );
          })}
          <div className="col-span-2 border-t border-border pt-2">
            <Button size="sm" variant="ghost" onClick={() => onChange(DEFAULT_COLUMNS)}>
              Reset to default columns
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

/** The glossary entry that explains a column in plain English, where there is one. */
const FIELD_HELP: Partial<Record<Field, GlossaryKey>> = {
  group: "group_rank",
  group_rank: "group_rank",
  volume_ratio: "volume_ratio",
  rs_rating: "rs_rating",
  rs_line_high: "rs_line",
  rs_ahead: "rs_line",
  stage: "stage",
  tt_passed: "trend_template",
  tt_pass: "trend_template",
  fund_grade: "fundamentals",
  setup_state: "setup_stage",
  pattern: "base",
  grade: "setup_score",
  score: "setup_score",
  readiness_pct: "pivot",
  pivot: "pivot",
  breakout_today: "breakout",
  pocket_pivot_today: "pocket_pivot",
};

/** "What do the columns mean?": every column on screen, in plain English (Phase 8). */
export function ColumnGuide({ columns }: { columns: Field[] }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <Button size="sm" variant="ghost">
          What do the columns mean?
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          aria-label="What the columns mean"
          className="z-30 flex max-h-[min(32rem,var(--radix-popover-content-available-height))] w-[min(32rem,95vw)] flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-surface p-3 text-sm shadow-lg"
        >
          <dl className="flex flex-col gap-3">
            {columns.map((f) => {
              const help = FIELD_HELP[f];
              return (
                <div key={f}>
                  <dt className="font-medium">{FIELDS[f].label}</dt>
                  <dd className="text-muted">
                    {FIELDS[f].description}
                    {help && ` ${GLOSSARY[help].plain}`}
                  </dd>
                </div>
              );
            })}
          </dl>
          <Link href="/guide" className="text-foreground underline underline-offset-2">
            Every term, and how to use the app
          </Link>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

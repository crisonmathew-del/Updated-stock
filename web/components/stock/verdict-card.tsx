"use client";

import { InfoTip } from "@/components/ui/info-tip";
import { Section, StatusMark } from "@/components/ui/section";
import type { Action, EarlyEntry, Order, SetupDetail, Watch, WatchPart } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Each action's mark and colour (the mark carries the meaning; the colour repeats it). */
export const ACTION_LOOK: Record<Action, { icon: string; tone: string }> = {
  buy: { icon: "✓", tone: "text-rise" },
  get_ready: { icon: "◎", tone: "text-tide-ink" },
  wait: { icon: "…", tone: "text-muted" },
  avoid: { icon: "✕", tone: "text-fall" },
};

const ORDER_TITLES: Record<Order["type"], string> = {
  buy_stop: "Your order for the next session",
  buy_now: "Buy now",
  on_volume: "Wait for heavy buying",
};

/** "✓ Buy signal" as a compact label for tables. */
export function ActionLabel({
  action,
  label,
  className,
}: {
  action: Action | null;
  label: string | null;
  className?: string;
}) {
  if (!action || !label) return <span className="text-muted">—</span>;
  const look = ACTION_LOOK[action];
  return (
    <span className={cn("whitespace-nowrap", look.tone, className)}>
      <span aria-hidden>{look.icon}</span> {label}
    </span>
  );
}

export function OrderBox({ order }: { order: Order }) {
  return (
    <div className="rounded-md border border-tide/60 bg-surface-2 p-3 text-sm">
      <p className="flex items-center gap-1.5 font-medium">
        {ORDER_TITLES[order.type]}
        {order.type === "buy_stop" && <InfoTip term="buy_stop" />}
      </p>
      <p className="mt-1">{order.text}</p>
    </div>
  );
}

/** What the app's rules say to do today, every rule with its numbers, and the stock in plain
 * English (spec §0: explainable). */
export function VerdictCard({ setup }: { setup: SetupDetail }) {
  const v = setup.verdict;
  if (!v) return null;
  const look = ACTION_LOOK[v.action];
  return (
    <Section title="What to do" help="verdict" note={`${v.passed} of ${v.total} rules pass`}>
      <p className={cn("flex items-center gap-2 text-xl font-semibold", look.tone)}>
        <span aria-hidden>{look.icon}</span>
        {v.headline}
      </p>
      <p className="text-sm">{v.reason}</p>
      {v.order && <OrderBox order={v.order} />}
      {v.notes.length > 0 && (
        <ul className="flex flex-col gap-1 text-sm text-muted">
          {v.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <ul aria-label="The app's buy rules" className="flex flex-col gap-1.5 text-sm">
        {v.checks.map((c) => (
          <li key={c.key} className="flex gap-2">
            <StatusMark status={c.passed} />
            <span>
              <span className="font-medium">{c.label}</span>
              <span className="block text-muted">{c.detail}</span>
            </span>
          </li>
        ))}
      </ul>
      {v.plain && (
        <div className="flex flex-col gap-1 border-t border-border pt-3 text-sm">
          <p className="text-xs font-semibold tracking-wide text-muted uppercase">
            In plain English
          </p>
          <p>{v.plain}</p>
        </div>
      )}
      <p className="text-xs text-muted">
        The app never places trades. You decide, and you place the order at your broker.
      </p>
    </Section>
  );
}

/** The readiness parts as bars, each with its numbers. */
export function WatchParts({ parts }: { parts: WatchPart[] }) {
  return (
    <ul aria-label="Signs of a coming breakout" className="flex flex-col gap-2 text-sm">
      {parts.map((p) => {
        const share = p.max_points > 0 ? (p.points / p.max_points) * 100 : 0;
        return (
          <li key={p.key} className="flex flex-col gap-1">
            <div className="flex justify-between gap-2">
              <span>{p.label}</span>
              <span className="tabular text-muted">
                {p.max_points > 0
                  ? `${p.points.toFixed(0)} / ${p.max_points.toFixed(0)}`
                  : "no data"}
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-surface-2" aria-hidden>
              <div className="h-1.5 rounded-full bg-tide" style={{ width: `${share}%` }} />
            </div>
            <span className="text-xs text-muted">{p.detail}</span>
          </li>
        );
      })}
    </ul>
  );
}

export function EarlyEntryBox({ entry }: { entry: EarlyEntry }) {
  return (
    <div className="rounded-md border border-warn/60 bg-surface-2 p-3 text-sm">
      <p className="flex items-center gap-1.5 font-medium">
        Early entry (riskier)
        <InfoTip term="early_entry" />
      </p>
      <p className="mt-1">{entry.text}</p>
    </div>
  );
}

/** How likely the base looks to break out soon, part by part, and any early entry. */
export function WatchCard({ watch }: { watch: Watch }) {
  return (
    <Section
      title="Breakout watch"
      help="breakout_watch"
      note={watch.on_watch ? "On the watch list" : "Not on the watch list"}
    >
      <p className="text-sm">
        <span className="tabular text-xl font-semibold">{watch.score.toFixed(0)}</span>
        <span className="text-muted">/100 readiness: how ready it looks to break out soon</span>
      </p>
      <WatchParts parts={watch.parts} />
      {watch.early_entry && <EarlyEntryBox entry={watch.early_entry} />}
      <p className="text-xs text-muted">
        A ranking of the usual signs, not a prediction: the Buy list shows how often its picks
        really broke out.
      </p>
    </Section>
  );
}

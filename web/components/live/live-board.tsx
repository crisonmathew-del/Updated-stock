"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { STATUS } from "@/components/alerts/queries";
import { Change, GradeBadge, StageBadge } from "@/components/ui/badges";
import { Section } from "@/components/ui/section";
import { StockLink } from "@/components/ui/stock-link";
import {
  api,
  type AlertsStatus,
  type LiveQuote,
  type ScanResult,
  type SetupEvent,
  type SetupList,
  type SetupRow,
} from "@/lib/api";
import { formatMarketTime, formatNumber, formatPrice } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useLive } from "@/stores/live";

const BOARD_STATES = "near_pivot,breakout,basing";

const EVENT_LOOK: Record<SetupEvent["kind"], { icon: string; tone: string; label: string }> = {
  breakout_provisional: { icon: "▲", tone: "text-rise", label: "Breaking out (provisional)" },
  breakout_extended: { icon: "◆", tone: "text-warn", label: "Past the buy zone" },
  setup_stop: { icon: "✕", tone: "text-fall", label: "Stop hit" },
};

export type BoardRow = {
  setup: SetupRow;
  quote: LiveQuote | undefined;
  last: number;
  fromPivot: number | null; // % above (+) or below (−) the pivot
  event: SetupEvent | undefined;
};

/** Setups in play with live prices: today's events first (provisional breakouts, then the
 * rest), then the closest to their pivot. */
export function boardRows(
  setups: SetupRow[],
  quotes: Record<string, LiveQuote>,
  events: SetupEvent[],
): BoardRow[] {
  const rank = { breakout_provisional: 0, setup_stop: 1, breakout_extended: 2 };
  return setups
    .map((setup) => {
      const quote = quotes[setup.symbol];
      const last = quote?.last ?? setup.close;
      const event = events.find((e) => e.symbol === setup.symbol);
      return {
        setup,
        quote,
        last,
        fromPivot: setup.pivot ? (last / setup.pivot - 1) * 100 : null,
        event,
      };
    })
    .sort((a, b) => {
      const ea = a.event ? rank[a.event.kind] : 9;
      const eb = b.event ? rank[b.event.kind] : 9;
      if (ea !== eb) return ea - eb;
      return Math.abs(a.fromPivot ?? 99) - Math.abs(b.fromPivot ?? 99);
    });
}

function FeedNote() {
  const connected = useLive((s) => s.connected);
  const quoted = useLive((s) => Object.keys(s.quotes).length > 0);
  const status = useQuery({
    queryKey: STATUS.key,
    queryFn: () => api.get<AlertsStatus>(STATUS.path),
    refetchInterval: 30_000,
  });
  const s = status.data?.streamer;
  // Quotes arriving count as streaming even before the status is refetched.
  const streaming =
    s?.state === "streaming" || (connected && quoted && s?.state !== "replay finished");
  const provider =
    s?.provider === "alpaca" ? "Alpaca" : s?.provider && s.provider !== "none" ? s.provider : null;
  return (
    <p role="status" className="text-xs text-muted">
      <span aria-hidden className={connected && streaming ? "text-rise" : "text-muted"}>
        ●
      </span>{" "}
      {!connected
        ? "Connecting to live updates…"
        : streaming
          ? `Live${provider ? ` from ${provider}` : ""}. Intraday volume is provisional until the close confirms it.`
          : s?.state === "replay finished"
            ? "The replay has finished."
            : "No live feed right now: prices are the last close. "}
      {connected && !streaming && s?.state !== "replay finished" && (
        <Link href="/alerts?tab=channels" className="text-tide-ink hover:underline">
          Why?
        </Link>
      )}
    </p>
  );
}

function Price({ quote, fallback }: { quote: LiveQuote | undefined; fallback: number }) {
  // Keyed by the price so each change restarts the flash (CSS; none under reduced motion).
  return (
    <span
      key={quote?.last ?? "close"}
      className={cn("tabular rounded px-1", quote && "live-flash")}
    >
      {formatPrice(quote?.last ?? fallback)}
    </span>
  );
}

function SetupsTable() {
  const setups = useQuery({
    queryKey: ["setups", "live", BOARD_STATES],
    queryFn: () => api.get<SetupList>(`/api/setups?state=${BOARD_STATES}&limit=200`),
    staleTime: 5 * 60_000,
  });
  const quotes = useLive((s) => s.quotes);
  const events = useLive((s) => s.events);
  const rows = boardRows(setups.data?.items ?? [], quotes, events);
  const symbols = rows.map((r) => r.setup.symbol);
  return (
    <Section
      title="Setups in play"
      note={setups.data?.as_of ? `levels as of ${setups.data.as_of}` : undefined}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[460px] text-sm sm:min-w-[720px]">
          <caption className="sr-only">Setups near or past their pivot, with live prices</caption>
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="py-1.5 pr-3 text-left font-medium">Stock</th>
              <th className="py-1.5 pr-3 text-left font-medium">Setup</th>
              <th className="py-1.5 pr-3 text-right font-medium">Pivot</th>
              <th className="py-1.5 pr-3 text-right font-medium">Last</th>
              <th className="py-1.5 pr-3 text-right font-medium">vs pivot</th>
              <th className="py-1.5 pr-3 text-right font-medium">Day</th>
              <th className="py-1.5 text-left font-medium">Now</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ setup, quote, last, fromPivot, event }) => {
              const look = event ? EVENT_LOOK[event.kind] : undefined;
              return (
                <tr key={setup.id} className="border-b border-border last:border-0">
                  <td className="py-1.5 pr-3">
                    <StockLink
                      symbol={setup.symbol}
                      list={{ source: "Live", symbols }}
                      className="font-semibold text-tide-ink hover:underline"
                    >
                      {setup.symbol}
                    </StockLink>
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className="flex items-center gap-2">
                      <GradeBadge grade={setup.grade} score={setup.score} />
                      <span className="hidden text-muted sm:inline">
                        {setup.pattern_label ?? setup.kind}
                      </span>
                    </span>
                  </td>
                  <td className="tabular py-1.5 pr-3 text-right">{formatPrice(setup.pivot)}</td>
                  <td className="py-1.5 pr-3 text-right">
                    <Price quote={quote} fallback={last} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    <Change value={fromPivot} digits={1} />
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {quote ? (
                      <Change value={quote.change_pct} />
                    ) : (
                      <span className="text-muted">—</span>
                    )}
                  </td>
                  <td className="py-1.5">
                    {look && event ? (
                      <span className={cn("whitespace-nowrap", look.tone)} title={event.title}>
                        <span aria-hidden>{look.icon}</span> {look.label}{" "}
                        <span className="text-xs text-muted">{formatMarketTime(event.at)}</span>
                      </span>
                    ) : (
                      <StageBadge state={setup.state} />
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {setups.isSuccess && rows.length === 0 && (
          <p className="py-4 text-sm text-muted">No setups near a pivot right now.</p>
        )}
      </div>
    </Section>
  );
}

function ScanTable({
  title,
  scan,
  kind,
}: {
  title: string;
  scan: ScanResult | null;
  kind: "premarket" | "sweep";
}) {
  const items = scan?.items ?? [];
  const symbols = items.map((i) => i.symbol);
  return (
    <Section title={title} note={scan ? `as of ${formatMarketTime(scan.at)}` : "not run yet today"}>
      {items.length === 0 ? (
        <p className="text-sm text-muted">
          {kind === "premarket"
            ? "Gaps of 4% or more on real pre-market volume show up here from 08:00 ET."
            : "Stocks moving 3% or more on twice their usual volume show up here during the session."}
        </p>
      ) : (
        <ul className="flex flex-col">
          {items.map((i) => (
            <li
              key={i.symbol}
              className="flex items-baseline gap-3 border-b border-border py-1.5 text-sm last:border-0"
            >
              <StockLink
                symbol={i.symbol}
                list={{ source: title, symbols }}
                className="w-16 font-semibold text-tide-ink hover:underline"
              >
                {i.symbol}
              </StockLink>
              <Change value={i.change_pct} digits={1} />
              <span className="tabular">{formatPrice(i.price)}</span>
              <span className="text-xs text-muted">
                {kind === "premarket"
                  ? `${formatNumber(Math.round(i.volume))} sh (${Math.round(i.volume_pct)}% of avg)`
                  : `${(i.volume_pct / 100).toFixed(1)}× avg volume`}
              </span>
              {i.earnings && <span className="text-xs text-warn">◆ earnings</span>}
              {i.grade && (
                <span className="ml-auto">
                  <GradeBadge grade={i.grade} />
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Events() {
  const events = useLive((s) => s.events);
  return (
    <Section title="Today's events">
      {events.length === 0 ? (
        <p className="text-sm text-muted">
          Provisional breakouts, moves past the buy zone and stop hits appear here as they happen.
        </p>
      ) : (
        <ol className="flex flex-col gap-1 text-sm">
          {events.map((e) => {
            const look = EVENT_LOOK[e.kind];
            return (
              <li key={`${e.kind}-${e.symbol}-${e.at}`} className="flex gap-2">
                <span className="tabular text-xs text-muted">{formatMarketTime(e.at)}</span>
                <span className={look.tone}>
                  <span aria-hidden>{look.icon}</span>
                </span>
                <Link href={`/stocks/${e.symbol}`} className="hover:underline">
                  {e.title}
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </Section>
  );
}

/** The live board (spec §8.2): setups in play with live prices and today's intraday events,
 * plus the pre-market scan and the intraday sweep. */
export function LiveBoard() {
  const scans = useLive((s) => s.scans);
  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-lg font-semibold">Live</h1>
        <FeedNote />
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <SetupsTable />
        <div className="flex min-w-0 flex-col gap-4">
          <Events />
          <ScanTable title="Pre-market movers" scan={scans.premarket} kind="premarket" />
          <ScanTable title="Intraday sweep" scan={scans.sweep} kind="sweep" />
        </div>
      </div>
    </main>
  );
}

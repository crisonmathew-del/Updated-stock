"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { shortDate } from "@/components/dashboard/market-panel";
import { ActionLabel, EarlyEntryBox, OrderBox, WatchParts } from "@/components/stock/verdict-card";
import { GradeBadge, StageBadge } from "@/components/ui/badges";
import { InfoTip } from "@/components/ui/info-tip";
import { Section } from "@/components/ui/section";
import { StockLink } from "@/components/ui/stock-link";
import { api, type BuyItem, type BuyList } from "@/lib/api";
import { formatPrice, formatReadiness } from "@/lib/format";
import { cn } from "@/lib/utils";
import { BUY_LIST, BUY_LIST_STALE_MS } from "./queries";

export function useBuyList() {
  return useQuery({
    queryKey: BUY_LIST.key,
    queryFn: () => api.get<BuyList>(BUY_LIST.path),
    staleTime: BUY_LIST_STALE_MS,
  });
}

const MARKET_LOOK: Record<string, { icon: string; tone: string }> = {
  confirmed_uptrend: { icon: "▲", tone: "border-rise/60 text-rise" },
  uptrend_under_pressure: { icon: "◆", tone: "border-warn/60 text-warn" },
  correction: { icon: "▼", tone: "border-fall/60 text-fall" },
};

/** The first rule: whether the market allows new buys at all. */
export function MarketBanner({ market }: { market: BuyList["market"] }) {
  const look = MARKET_LOOK[market.state ?? ""] ?? { icon: "•", tone: "border-border text-muted" };
  return (
    <section
      aria-label="The market today"
      className={cn("flex items-start gap-3 rounded-lg border bg-surface p-4", look.tone)}
    >
      <span aria-hidden className="text-lg leading-6">
        {look.icon}
      </span>
      <div className="flex flex-col gap-1 text-foreground">
        <p className="flex items-center gap-1.5 font-semibold">
          Market: {market.label ?? "not known yet"}
          <InfoTip term="market_regime" />
        </p>
        <p className="text-sm">{market.note}</p>
      </div>
    </section>
  );
}

function facts(item: BuyItem): string {
  return [
    item.pattern_label,
    item.pivot != null ? `pivot ${formatPrice(item.pivot)}` : null,
    item.readiness_pct != null ? formatReadiness(item.readiness_pct) : null,
    `closed at ${formatPrice(item.close)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

type List = { source: string; symbols: string[] };

function OrderCard({ item, list }: { item: BuyItem; list: List }) {
  return (
    <li className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <StockLink symbol={item.symbol} list={list} className="min-w-0 hover:underline">
          <span className="text-lg font-semibold">{item.symbol}</span>
          <span className="block truncate text-sm text-muted">{item.name}</span>
        </StockLink>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <ActionLabel action={item.action} label={item.action_label} className="font-medium" />
          <GradeBadge grade={item.grade} score={item.score} />
        </div>
      </div>
      {item.action_reason && <p className="text-sm">{item.action_reason}</p>}
      {item.order && <OrderBox order={item.order} />}
      <p className="text-xs text-muted">{facts(item)}</p>
      {item.plain && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted hover:text-foreground">
            In plain English
          </summary>
          <p className="mt-2">{item.plain}</p>
        </details>
      )}
    </li>
  );
}

function WatchRow({ item, list }: { item: BuyItem; list: List }) {
  const why =
    item.failed.length > 0 ? `Not a buy yet: ${item.failed.join("; ")}.` : item.action_reason;
  const score = item.watch_score ?? 0;
  return (
    <li className="flex flex-col gap-2 border-b border-border py-3 last:border-0">
      <StockLink
        symbol={item.symbol}
        list={list}
        className="-mx-2 grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 rounded px-2 py-1 hover:bg-surface-2 focus-visible:bg-surface-2 sm:grid-cols-[minmax(0,1fr)_8rem_8.5rem_7rem_8rem]"
      >
        <span className="min-w-0">
          <span className="font-medium">{item.symbol}</span>
          <span className="block truncate text-xs text-muted">{item.name}</span>
        </span>
        <span className="flex flex-col gap-1">
          <span className="text-sm">
            <span className="tabular font-semibold">{score.toFixed(0)}</span>
            <span className="text-muted">/100 ready</span>
          </span>
          <span className="h-1.5 rounded-full bg-surface-2" aria-hidden>
            <span
              className="block h-1.5 rounded-full bg-tide"
              style={{ width: `${Math.min(100, score)}%` }}
            />
          </span>
        </span>
        <span className="tabular text-sm">
          <span className="text-tide-ink">{formatReadiness(item.readiness_pct)}</span>
          <span className="block text-xs text-muted">pivot {formatPrice(item.pivot)}</span>
        </span>
        <StageBadge state={item.state} className="text-sm" />
        <ActionLabel
          action={item.action}
          label={item.action_label}
          className="text-sm sm:text-right"
        />
      </StockLink>
      {why && <p className="text-sm text-muted">{why}</p>}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted hover:text-foreground">
          Why it&rsquo;s on the watch{item.early_entry ? ", and an earlier entry" : ""}
        </summary>
        <div className="mt-3 flex max-w-xl flex-col gap-3">
          <WatchParts parts={item.watch_parts} />
          {item.early_entry && <EarlyEntryBox entry={item.early_entry} />}
        </div>
      </details>
    </li>
  );
}

/** "1 session", "2.5 sessions". */
function sessions(n: number): string {
  return `${Number(n.toFixed(1))} session${n === 1 ? "" : "s"}`;
}

/** How often the watch's picks really broke out (honest: misses count too). */
export function TrackRecord({ track }: { track: BuyList["track"] }) {
  const within = `${track.within_sessions} sessions`;
  if (track.picks === 0) {
    return (
      <p className="text-sm text-muted">
        No track record yet. It starts with the first evening scan that puts a stock on the watch: a
        pick counts as a hit if it breaks out within {within}.
      </p>
    );
  }
  return (
    <p className="text-sm">
      Track record since {shortDate(track.since)}: {track.picks}{" "}
      {track.picks === 1 ? "pick" : "picks"}.{" "}
      <span className="text-rise">
        <span aria-hidden>✓</span> {track.hits} broke out within {within}
      </span>
      ,{" "}
      <span className="text-fall">
        <span aria-hidden>✕</span> {track.misses} didn&rsquo;t
      </span>
      , {track.pending} still waiting.{" "}
      {track.hit_rate_pct != null ? (
        <>
          Hit rate <span className="tabular font-semibold">{track.hit_rate_pct.toFixed(0)}%</span>
          {track.median_sessions_to_breakout != null &&
            ` (a median of ${sessions(track.median_sessions_to_breakout)} to the breakout)`}
          .
        </>
      ) : (
        `No hit rate yet: no pick has had ${within} to break out.`
      )}{" "}
      <span className="text-muted">Past hits don&rsquo;t promise future ones.</span>
    </p>
  );
}

/**
 * The buy list (Phase 8): the market's verdict, the orders to place for the next session and the
 * breakout watch, with its track record. Everything comes from the evening scan.
 */
export function BuyListView() {
  const q = useBuyList();
  const data = q.data;
  const orders = data?.orders ?? [];
  const watch = data?.watch ?? [];
  const orderList = { source: "Buy list", symbols: orders.map((o) => o.symbol) };
  const watchList = { source: "Breakout watch", symbols: watch.map((o) => o.symbol) };
  return (
    <>
      <header className="flex flex-col gap-1">
        <h1 className="text-xl font-semibold">Buy list</h1>
        <p className="max-w-3xl text-sm text-muted">
          What the app&rsquo;s rules say to do next
          {data?.as_of ? `, from the close on ${shortDate(data.as_of)}` : ""}. It updates every
          evening after the US close. The app never places trades: you decide, and you place the
          orders at your broker.{" "}
          <Link href="/guide" className="text-foreground underline underline-offset-2">
            New here? Start here
          </Link>
        </p>
      </header>
      {q.isPending && <p className="text-sm text-muted">Loading…</p>}
      {q.error && <p className="text-sm text-fall">{q.error.message}</p>}
      {data && (
        <>
          <MarketBanner market={data.market} />
          <Section
            title="Orders for the next session"
            help="buy_stop"
            note={orders.length > 0 ? `${orders.length} to place` : undefined}
          >
            {orders.length === 0 ? (
              <p className="text-sm text-muted">
                {data.market.allows_buys
                  ? "No orders tonight: no stock passes all 7 rules. That's normal, good setups are rare. The breakout watch below shows the closest candidates."
                  : "No orders tonight: the market rule says no new buys (above). The breakout watch below shows what to be ready for."}
              </p>
            ) : (
              <ul aria-label="Orders to place" className="grid gap-3 lg:grid-cols-2">
                {orders.map((item) => (
                  <OrderCard key={item.id} item={item} list={orderList} />
                ))}
              </ul>
            )}
          </Section>
          <Section
            title="Breakout watch"
            help="breakout_watch"
            note="Most ready to break out first"
          >
            <p className="text-sm text-muted">
              Bases showing the signs that often come before a breakout. It&rsquo;s a ranking, not a
              prediction: buy only when one breaks out and passes the rules (it then moves up to the
              orders).
            </p>
            <TrackRecord track={data.track} />
            {watch.length === 0 ? (
              <p className="text-sm text-muted">
                Nothing on the watch tonight: no base is close enough to its pivot with enough of
                the signs.
              </p>
            ) : (
              <ul aria-label="Stocks on the breakout watch" className="flex flex-col">
                {watch.map((item) => (
                  <WatchRow key={item.id} item={item} list={watchList} />
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </>
  );
}

/** The dashboard's short version: the market's word, the orders and the top of the watch. */
export function BuyListPanel({ className }: { className?: string }) {
  const q = useBuyList();
  const data = q.data;
  const orders = data?.orders ?? [];
  const watch = data?.watch ?? [];
  const list = { source: "Buy list", symbols: orders.map((o) => o.symbol) };
  return (
    <Section
      title="Buy list"
      help="verdict"
      note={<Link href="/buy-list">Open the Buy list →</Link>}
      className={className}
    >
      {q.isPending && <p className="text-sm text-muted">Loading…</p>}
      {q.error && <p className="text-sm text-fall">{q.error.message}</p>}
      {data && (
        <>
          <p className="text-sm">{data.market.note}</p>
          {orders.length === 0 ? (
            <p className="text-sm text-muted">No orders to place tonight.</p>
          ) : (
            <ul aria-label="Orders to place" className="-mx-2 flex flex-col">
              {orders.slice(0, 5).map((o) => (
                <li key={o.id}>
                  <StockLink
                    symbol={o.symbol}
                    list={list}
                    className="grid grid-cols-[minmax(0,1fr)_auto_5.5rem] items-center gap-x-3 rounded px-2 py-1.5 text-sm hover:bg-surface-2 focus-visible:bg-surface-2"
                  >
                    <span className="min-w-0">
                      <span className="font-medium">{o.symbol}</span>
                      <span className="block truncate text-xs text-muted">{o.name}</span>
                    </span>
                    <ActionLabel action={o.action} label={o.action_label} className="text-xs" />
                    <span className="tabular text-right text-xs">
                      {o.order ? formatPrice(o.order.price) : "—"}
                      <span className="block text-muted">
                        {o.order ? `${o.order.shares} shares` : ""}
                      </span>
                    </span>
                  </StockLink>
                </li>
              ))}
            </ul>
          )}
          <p className="text-sm text-muted">
            {watch.length === 0
              ? "Nothing on the breakout watch."
              : `${watch.length} on the breakout watch, most ready: ${watch
                  .slice(0, 3)
                  .map((w) => w.symbol)
                  .join(", ")}.`}
          </p>
        </>
      )}
    </Section>
  );
}

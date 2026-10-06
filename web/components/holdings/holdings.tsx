"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { HOLDINGS } from "@/components/alerts/queries";
import { Change } from "@/components/ui/badges";
import { Button } from "@/components/ui/button";
import { StockLink } from "@/components/ui/stock-link";
import { api, type Holding, type HoldingInput } from "@/lib/api";
import { formatNumber, formatPrice, formatR } from "@/lib/format";
import { livePosition, totals } from "@/lib/positions";
import { size, sizingSettings } from "@/lib/sizing";
import { cn } from "@/lib/utils";
import { useLive } from "@/stores/live";
import { useToasts } from "@/stores/toast";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";
const money = (x: number | null | undefined) =>
  x == null ? "—" : `${x < 0 ? "−" : ""}$${formatNumber(Math.round(Math.abs(x)))}`;

function today(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
}

/** Add a position by hand. Leave shares empty to size it from your account settings (the trade
 * plan's rules: risk per trade ÷ risk per share, capped by the largest position). */
function AddForm() {
  const params = useSearchParams();
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ items: { key: string; value: unknown }[] }>("/api/settings"),
  });
  const [symbol, setSymbol] = useState((params.get("symbol") ?? "").toUpperCase());
  const [entry, setEntry] = useState(params.get("entry") ?? "");
  const [stop, setStop] = useState(params.get("stop") ?? "");
  const [shares, setShares] = useState(params.get("shares") ?? "");
  const [opened, setOpened] = useState(today());
  const sized =
    settings.data && entry && stop
      ? size(Number(entry), Number(stop), sizingSettings(settings.data.items))
      : null;
  const add = useMutation({
    mutationFn: (body: HoldingInput) => api.post<Holding>("/api/holdings", body),
    onSuccess: (h) => {
      void client.invalidateQueries({ queryKey: HOLDINGS });
      push(`Added ${h.shares} ${h.symbol} at ${formatPrice(h.entry_price)}.`);
      setSymbol("");
      setEntry("");
      setStop("");
      setShares("");
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    const count = shares ? Number(shares) : (sized?.shares ?? 0);
    add.mutate({
      symbol,
      entry_price: Number(entry),
      initial_stop: Number(stop),
      shares: count,
      opened_on: opened,
      setup_id: params.get("setup") ? Number(params.get("setup")) : null,
    });
  }

  return (
    <form
      aria-label="Add a position"
      onSubmit={submit}
      className="flex flex-col gap-2 rounded-lg border border-border bg-surface p-4"
    >
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Stock</span>
          <input
            required
            value={symbol}
            maxLength={16}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className={`${FIELD} w-24 uppercase`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Entry</span>
          <input
            required
            type="number"
            step="any"
            min="0"
            inputMode="decimal"
            value={entry}
            onChange={(e) => setEntry(e.target.value)}
            className={`${FIELD} tabular w-24`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Stop</span>
          <input
            required
            type="number"
            step="any"
            min="0"
            inputMode="decimal"
            value={stop}
            onChange={(e) => setStop(e.target.value)}
            className={`${FIELD} tabular w-24`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Shares</span>
          <input
            type="number"
            min="1"
            step="1"
            value={shares}
            placeholder={sized ? String(sized.shares) : ""}
            onChange={(e) => setShares(e.target.value)}
            className={`${FIELD} tabular w-24`}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-muted">Bought on</span>
          <input
            type="date"
            value={opened}
            onChange={(e) => setOpened(e.target.value)}
            className={FIELD}
          />
        </label>
        <Button type="submit" variant="primary" disabled={add.isPending}>
          Add position
        </Button>
      </div>
      {sized && !shares && (
        <p className="text-xs text-muted">
          Sized from your settings: {sized.shares} shares risk {money(sized.dollarRisk)} (
          {formatPrice(sized.riskPerShare)} a share, {sized.riskPct}%)
          {sized.cappedByPosition ? ", capped by the largest position" : ""}.
        </p>
      )}
      {add.isError && (
        <p role="alert" className="text-sm text-fall">
          {add.error.message}
        </p>
      )}
    </form>
  );
}

function Warnings({ h }: { h: Holding }) {
  if (h.warnings.length === 0) return <span className="text-muted">—</span>;
  return (
    <ul className="flex flex-col gap-0.5">
      {h.warnings.map((w) => (
        <li
          key={w.rule}
          title={w.body}
          className={cn("text-xs", w.priority === "high" ? "text-fall" : "text-warn")}
        >
          <span aria-hidden>{w.priority === "high" ? "▼" : "◆"}</span>{" "}
          {w.title.replace(`${h.symbol} `, "")}
        </li>
      ))}
    </ul>
  );
}

function Row({ holding: h, symbols }: { holding: Holding; symbols: string[] }) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [editing, setEditing] = useState<"stop" | "close" | "delete" | null>(null);
  const [value, setValue] = useState("");
  const done = () => {
    setEditing(null);
    void client.invalidateQueries({ queryKey: HOLDINGS });
  };
  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.patch<Holding>(`/api/holdings/${h.id}`, body),
    onSuccess: done,
    onError: (error) => push(error.message, "error"),
  });
  const remove = useMutation({
    mutationFn: () => api.delete(`/api/holdings/${h.id}`),
    onSuccess: () => {
      done();
      push(`Removed ${h.symbol}.`);
    },
    onError: (error) => push(error.message, "error"),
  });
  const closed = h.closed_on != null;

  return (
    <tr className="border-b border-border align-top last:border-0">
      <td className="py-2 pr-3">
        <StockLink
          symbol={h.symbol}
          list={{ source: "Holdings", symbols }}
          className="font-semibold text-tide-ink hover:underline"
        >
          {h.symbol}
        </StockLink>
        <div className="max-w-40 truncate text-xs text-muted">{h.name}</div>
      </td>
      <td className="tabular py-2 pr-3 text-right">{formatNumber(h.shares)}</td>
      <td className="tabular py-2 pr-3 text-right">{formatPrice(h.entry_price)}</td>
      <td className="tabular py-2 pr-3 text-right">
        {formatPrice(h.stop)}
        {h.stop !== h.initial_stop && (
          <div className="text-xs text-muted">from {formatPrice(h.initial_stop)}</div>
        )}
      </td>
      <td className="tabular py-2 pr-3 text-right">
        {formatPrice(h.price)}
        <div className="text-xs text-muted">
          {h.price_source === "live" ? (
            <>
              <span aria-hidden className="text-rise">
                ●
              </span>{" "}
              live
            </>
          ) : h.price_source === "exit" ? (
            `sold ${h.closed_on}`
          ) : (
            "close"
          )}
        </div>
      </td>
      <td className="py-2 pr-3 text-right">{closed ? "—" : <Change value={h.day_change_pct} />}</td>
      <td className="py-2 pr-3 text-right">
        <Change value={h.pnl} suffix="" prefix="$" digits={0} />
        <div>
          <Change value={h.pnl_pct} className="text-xs" />
        </div>
      </td>
      <td className="tabular py-2 pr-3 text-right font-medium">
        <span
          className={cn(
            h.r != null && h.r > 0 && "text-rise",
            h.r != null && h.r < 0 && "text-fall",
          )}
        >
          {h.r == null ? "—" : formatR(h.r)}
        </span>
      </td>
      <td className="tabular py-2 pr-3 text-right">{closed ? "—" : money(h.open_risk)}</td>
      <td className="py-2 pr-3">
        <Warnings h={h} />
      </td>
      <td className="py-2 text-right whitespace-nowrap">
        {editing === "stop" || editing === "close" ? (
          <form
            className="flex items-center justify-end gap-1"
            onSubmit={(event) => {
              event.preventDefault();
              patch.mutate(
                editing === "stop" ? { stop: Number(value) } : { exit_price: Number(value) },
              );
            }}
          >
            <input
              autoFocus
              aria-label={
                editing === "stop" ? `New stop for ${h.symbol}` : `Exit price for ${h.symbol}`
              }
              type="number"
              step="any"
              min="0"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              className={`${FIELD} tabular w-20`}
            />
            <Button size="sm" type="submit" variant="primary" disabled={!value || patch.isPending}>
              {editing === "stop" ? "Set" : "Close"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
          </form>
        ) : editing === "delete" ? (
          <span className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Keep
            </Button>
            <Button size="sm" className="text-fall" onClick={() => remove.mutate()}>
              Delete {h.symbol}
            </Button>
          </span>
        ) : closed ? (
          <span className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => patch.mutate({ closed_on: null })}>
              Reopen
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing("delete")}>
              Delete
            </Button>
          </span>
        ) : (
          <span className="flex justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setValue(String(h.stop));
                setEditing("stop");
              }}
            >
              Move stop
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setValue(h.price != null ? String(h.price) : "");
                setEditing("close");
              }}
            >
              Sell
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing("delete")}>
              Delete
            </Button>
          </span>
        )}
      </td>
    </tr>
  );
}

/**
 * Holdings (spec §8.6): positions you entered, priced live when the streamer has a quote, with
 * P&L in dollars, % and R (against the initial stop), the risk still open to your stop, and the
 * sell rules that apply (stop, 50-day and 21-day breaks, breakeven, profit zone, earnings).
 */
export function Holdings() {
  const [showClosed, setShowClosed] = useState(false);
  const holdings = useQuery({
    queryKey: [...HOLDINGS, { closed: showClosed }],
    queryFn: () => api.get<Holding[]>(`/api/holdings${showClosed ? "?closed=true" : ""}`),
    refetchInterval: 60_000,
  });
  const quotes = useLive((s) => s.quotes);
  const rows = (holdings.data ?? []).map((h) => livePosition(h, quotes[h.symbol]));
  const open = rows.filter((h) => h.closed_on == null);
  const sum = totals(open);
  const symbols = rows.map((h) => h.symbol);

  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-col gap-4 px-4 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="text-lg font-semibold">Holdings</h1>
        <p className="text-xs text-muted">
          Entered by hand; Breakout never trades. R is measured against the initial stop.
        </p>
      </div>
      <AddForm />
      {open.length > 0 && (
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-muted">Positions</dt>
            <dd className="tabular">{sum.count}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Value</dt>
            <dd className="tabular">{money(sum.value)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Open P&amp;L</dt>
            <dd>
              <Change value={sum.pnl} suffix="" prefix="$" digits={0} />
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Risk to stops</dt>
            <dd className="tabular">{money(sum.openRisk)}</dd>
          </div>
        </dl>
      )}
      <div className="overflow-x-auto rounded-lg border border-border bg-surface px-3">
        <table className="w-full min-w-[960px] text-sm">
          <caption className="sr-only">Your positions</caption>
          <thead>
            <tr className="border-b border-border text-xs text-muted">
              <th className="py-2 pr-3 text-left font-medium">Stock</th>
              <th className="py-2 pr-3 text-right font-medium">Shares</th>
              <th className="py-2 pr-3 text-right font-medium">Entry</th>
              <th className="py-2 pr-3 text-right font-medium">Stop</th>
              <th className="py-2 pr-3 text-right font-medium">Price</th>
              <th className="py-2 pr-3 text-right font-medium">Day</th>
              <th className="py-2 pr-3 text-right font-medium">P&amp;L</th>
              <th className="py-2 pr-3 text-right font-medium">R</th>
              <th className="py-2 pr-3 text-right font-medium">Risk to stop</th>
              <th className="py-2 pr-3 text-left font-medium">Sell rules</th>
              <th className="py-2 text-right font-medium">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <Row key={h.id} holding={h} symbols={symbols} />
            ))}
          </tbody>
        </table>
        {holdings.isSuccess && rows.length === 0 && (
          <p className="py-6 text-center text-sm text-muted">
            No positions yet. Add one above, or use &ldquo;I bought this&rdquo; on a stock&apos;s
            trade plan.
          </p>
        )}
      </div>
      <label className="flex items-center gap-2 self-start text-sm">
        <input
          type="checkbox"
          checked={showClosed}
          onChange={(e) => setShowClosed(e.target.checked)}
        />
        Show closed positions
      </label>
    </main>
  );
}

"use client";

import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ALERTS, alertsPath, UNREAD, type AlertFilters } from "@/components/alerts/queries";
import { Button } from "@/components/ui/button";
import { api, type Alert, type AlertsPage } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";

/** Each channel's outcome as a short label with a mark (never colour alone) and the full
 * wording as a tooltip. */
export function deliveryLabel(
  channel: "in_app" | "email",
  value: string | undefined,
): { text: string; mark: "✓" | "✕" | "…" | "–"; tone: string } {
  const name = channel === "in_app" ? "In-app" : "Email";
  if (!value) return { text: `${name} –`, mark: "–", tone: "text-muted" };
  if (value === "sent") return { text: `${name} sent`, mark: "✓", tone: "text-rise" };
  if (value.endsWith("digest: sent"))
    return { text: `${name} in the ${value.split(" ")[0]} digest`, mark: "✓", tone: "text-rise" };
  if (value === "queued") return { text: `${name} sending`, mark: "…", tone: "text-muted" };
  if (value === "digest")
    return { text: `${name} in the next digest`, mark: "…", tone: "text-muted" };
  if (value.startsWith("held"))
    return { text: `${name} held (quiet hours)`, mark: "…", tone: "text-warn" };
  if (value.startsWith("failed")) return { text: `${name} failed`, mark: "✕", tone: "text-fall" };
  if (value.startsWith("not configured"))
    return { text: `${name} not set up`, mark: "–", tone: "text-muted" };
  return { text: `${name} off`, mark: "–", tone: "text-muted" };
}

function Delivery({ alert }: { alert: Alert }) {
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
      {(["in_app", "email"] as const).map((channel) => {
        const look = deliveryLabel(channel, alert.delivery[channel]);
        return (
          <span key={channel} title={alert.delivery[channel] ?? ""} className="whitespace-nowrap">
            <span aria-hidden className={look.tone}>
              {look.mark}
            </span>{" "}
            {look.text}
          </span>
        );
      })}
    </span>
  );
}

export function AlertRow({ alert, onRead }: { alert: Alert; onRead: (id: number) => void }) {
  return (
    <li className="flex gap-3 border-b border-border px-1 py-3 last:border-0">
      <span
        role={alert.read ? undefined : "img"}
        aria-label={alert.read ? undefined : "Unread"}
        className={cn("mt-1.5 size-2 shrink-0 rounded-full", !alert.read && "bg-tide")}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          {alert.priority === "high" && (
            <span className="text-xs text-warn">
              <span aria-hidden>▲</span> High
            </span>
          )}
          <span className="rounded border border-border px-1.5 text-xs text-muted">
            {alert.kind_label}
          </span>
          {alert.symbol && (
            <Link
              href={`/stocks/${alert.symbol}`}
              className="text-sm font-semibold text-tide-ink hover:underline"
            >
              {alert.symbol}
            </Link>
          )}
          <span className="tabular ml-auto text-xs text-muted">
            {formatDateTime(alert.created_at)}
          </span>
        </div>
        <p className={cn("text-sm", !alert.read && "font-medium")}>{alert.title}</p>
        <p className="text-sm text-muted">{alert.body}</p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Delivery alert={alert} />
          {!alert.read && (
            <button
              type="button"
              onClick={() => onRead(alert.id)}
              className="text-xs text-muted hover:text-foreground"
            >
              Mark read
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

/** Every alert raised for you, newest first, with what each channel did. */
export function AlertHistory() {
  const client = useQueryClient();
  const [filters, setFilters] = useState<AlertFilters>({});
  const history = useInfiniteQuery({
    queryKey: [...ALERTS, "list", "history", filters],
    queryFn: ({ pageParam }) => api.get<AlertsPage>(alertsPath(filters, pageParam)),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
  });
  const read = useMutation({
    mutationFn: (ids?: number[]) => api.post<{ count: number }>("/api/alerts/read", { ids }),
    onSuccess: (result) => {
      client.setQueryData(UNREAD.key, result);
      void client.invalidateQueries({ queryKey: [...ALERTS, "list"] });
    },
  });
  const pages = history.data?.pages ?? [];
  const alerts = pages.flatMap((p) => p.items);
  const first = pages[0];
  const set = (change: Partial<AlertFilters>) => setFilters((f) => ({ ...f, ...change }));
  const filtered = Boolean(filters.kind || filters.priority || filters.symbol || filters.unread);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted">Kind</span>
          <select
            value={filters.kind ?? ""}
            onChange={(e) => set({ kind: e.target.value || undefined })}
            className={FIELD}
          >
            <option value="">All</option>
            {first?.kinds.map((k) => (
              <option key={k.kind} value={k.kind}>
                {k.label} ({k.count})
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted">Priority</span>
          <select
            value={filters.priority ?? ""}
            onChange={(e) => set({ priority: e.target.value || undefined })}
            className={FIELD}
          >
            <option value="">Any</option>
            <option value="high">High</option>
            <option value="normal">Normal</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <span className="text-muted">Stock</span>
          <input
            value={filters.symbol ?? ""}
            onChange={(e) => set({ symbol: e.target.value.trim().toUpperCase() || undefined })}
            placeholder="Any"
            maxLength={16}
            className={cn(FIELD, "w-24 uppercase")}
          />
        </label>
        <label className="flex items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={Boolean(filters.unread)}
            onChange={(e) => set({ unread: e.target.checked || undefined })}
          />
          Unread only
        </label>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto"
          disabled={!first?.unread || read.isPending}
          onClick={() => read.mutate(undefined)}
        >
          Mark all read{first?.unread ? ` (${first.unread})` : ""}
        </Button>
      </div>

      {history.isError && (
        <p role="alert" className="text-sm text-fall">
          Couldn&apos;t load alerts: {history.error.message}
        </p>
      )}
      {history.isSuccess && alerts.length === 0 && (
        <p className="py-6 text-center text-sm text-muted">
          {filtered
            ? "No alerts match these filters."
            : "No alerts yet. Provisional breakouts, stops, the close and your rules will show up here."}
        </p>
      )}
      <ol aria-label="Alerts">
        {alerts.map((a) => (
          <AlertRow key={a.id} alert={a} onRead={(id) => read.mutate([id])} />
        ))}
      </ol>
      {history.hasNextPage && (
        <Button
          className="self-center"
          disabled={history.isFetchingNextPage}
          onClick={() => void history.fetchNextPage()}
        >
          {history.isFetchingNextPage ? "Loading…" : "Older alerts"}
        </Button>
      )}
    </div>
  );
}

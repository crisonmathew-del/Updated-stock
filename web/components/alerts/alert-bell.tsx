"use client";

import * as Popover from "@radix-ui/react-popover";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ALERTS, RECENT, UNREAD } from "@/components/alerts/queries";
import { api, type AlertsPage } from "@/lib/api";
import { formatAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

function BellIcon() {
  return (
    <svg aria-hidden viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor">
      <path
        d="M10 3a4.5 4.5 0 0 0-4.5 4.5v2.8L4 13h12l-1.5-2.7V7.5A4.5 4.5 0 0 0 10 3Z"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M8.2 15.5a1.9 1.9 0 0 0 3.6 0" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

/** The top bar's bell: unread count, the latest alerts, mark all read, the alerts centre. */
export function AlertBell() {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const unread = useQuery({
    queryKey: UNREAD.key,
    queryFn: () => api.get<{ count: number }>(UNREAD.path),
    staleTime: 60_000,
  });
  const recent = useQuery({
    queryKey: RECENT.key,
    queryFn: () => api.get<AlertsPage>(RECENT.path),
    enabled: open,
  });
  const markRead = useMutation({
    mutationFn: (ids?: number[]) => api.post<{ count: number }>("/api/alerts/read", { ids }),
    onSuccess: (result) => {
      queryClient.setQueryData(UNREAD.key, result);
      void queryClient.invalidateQueries({ queryKey: [...ALERTS, "list"] });
    },
  });
  const count = unread.data?.count ?? 0;
  const label = count ? `Alerts, ${count} unread` : "Alerts";

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          className="relative rounded-md p-1.5 text-muted hover:text-foreground"
        >
          <BellIcon />
          {count > 0 && (
            <span className="tabular absolute -top-0.5 -right-1 min-w-4 rounded-full bg-foreground px-1 text-center text-[10px] leading-4 font-semibold text-background">
              {count > 99 ? "99+" : count}
            </span>
          )}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={8}
          className="z-40 flex w-[min(24rem,calc(100vw-2rem))] flex-col rounded-md border border-border bg-surface text-sm shadow-lg"
        >
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <p className="font-medium">Alerts</p>
            <button
              type="button"
              disabled={count === 0 || markRead.isPending}
              onClick={() => markRead.mutate(undefined)}
              className="text-xs text-muted hover:text-foreground disabled:opacity-50"
            >
              Mark all read
            </button>
          </div>
          <ul className="max-h-[60vh] overflow-y-auto">
            {recent.isLoading && <li className="px-3 py-3 text-muted">Loading…</li>}
            {recent.data?.items.length === 0 && (
              <li className="px-3 py-3 text-muted">
                No alerts yet. Breakouts, stops and your rules will show up here.
              </li>
            )}
            {recent.data?.items.map((a) => (
              <li key={a.id} className="border-b border-border last:border-0">
                <Link
                  href={a.symbol ? `/stocks/${a.symbol}` : "/alerts"}
                  onClick={() => {
                    if (!a.read) markRead.mutate([a.id]);
                    setOpen(false);
                  }}
                  className="flex gap-2 px-3 py-2 hover:bg-surface-2"
                >
                  <span
                    aria-label={a.read ? undefined : "Unread"}
                    role={a.read ? undefined : "img"}
                    className={cn("mt-1.5 size-2 shrink-0 rounded-full", !a.read && "bg-tide")}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline justify-between gap-2">
                      <span className={cn("truncate", !a.read && "font-medium")}>
                        {a.priority === "high" && (
                          <span aria-label="High priority" role="img" className="text-warn">
                            ▲{" "}
                          </span>
                        )}
                        {a.title}
                      </span>
                      <span className="shrink-0 text-xs text-muted">{formatAgo(a.created_at)}</span>
                    </span>
                    <span className="line-clamp-2 text-xs text-muted">{a.body}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
          <Link
            href="/alerts"
            onClick={() => setOpen(false)}
            className="border-t border-border px-3 py-2 text-center text-xs text-tide-ink hover:bg-surface-2"
          >
            Open the alerts centre
          </Link>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

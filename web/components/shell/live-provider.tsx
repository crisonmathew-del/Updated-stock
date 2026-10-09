"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { ALERTS, HOLDINGS, LIVE, UNREAD } from "@/components/alerts/queries";
import { api, type Alert, type LiveEvent, type LiveSnapshot } from "@/lib/api";
import { connectLive } from "@/lib/live";
import { useLive } from "@/stores/live";
import { useToasts } from "@/stores/toast";

/** Pops a toast for an alert as it arrives: ▲ marks high priority; it links to the stock. */
export function toastAlert(alert: Alert) {
  const text = `${alert.priority === "high" ? "▲ " : ""}${alert.title}`;
  useToasts.getState().push(text, "alert", {
    href: alert.symbol ? `/stocks/${alert.symbol}` : "/alerts",
    ms: alert.priority === "high" ? 10_000 : 6000,
  });
}

/**
 * Keeps one live socket open while the app is shown: alerts update the bell, the alert lists
 * and pop a toast; quotes, setup events and scans go to the live store. On every (re)connect
 * the current quotes and scans are fetched, so nothing is missed while it was down. When new
 * analytics land (the evening update, a manual scan), or the socket comes back after they
 * did, every query is refetched and the processed session's live data is dropped, so an open
 * page never needs a reload.
 */
export function LiveProvider() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const live = useLive.getState();
    let through: string | null | undefined; // the processed close this page's data is from

    // `announced`: the server just stored new analytics (possibly a re-score of the same close).
    function onData(next: string | null, announced: boolean) {
      const moved = through !== undefined && next !== through;
      through = next;
      if (moved) live.clearSession();
      if (moved || announced) void queryClient.invalidateQueries();
    }

    async function prime() {
      try {
        const snapshot = await api.get<LiveSnapshot>(LIVE.path);
        onData(snapshot.through, false);
        live.applyQuotes(Object.values(snapshot.quotes));
        live.setEvents(snapshot.events ?? []);
        live.setScan("premarket", snapshot.premarket);
        live.setScan("sweep", snapshot.sweep);
      } catch {
        // The page still works without live data.
      }
    }

    function onEvent(event: LiveEvent) {
      if (event.type === "alert") {
        queryClient.setQueryData<{ count: number }>(UNREAD.key, (old) => ({
          count: (old?.count ?? 0) + (event.data.read ? 0 : 1),
        }));
        void queryClient.invalidateQueries({ queryKey: [...ALERTS, "list"] });
        if (event.data.kind.startsWith("holding")) {
          void queryClient.invalidateQueries({ queryKey: HOLDINGS });
        }
        toastAlert(event.data);
      } else if (event.type === "quotes") {
        live.applyQuotes(event.data);
      } else if (event.type === "setup_event") {
        live.addEvent(event.data);
      } else if (event.type === "scan") {
        live.setScan(event.scan, event.data);
      } else if (event.type === "data") {
        onData(event.through, true);
      }
    }

    return connectLive({
      onOpen: () => {
        live.setConnected(true);
        void prime();
      },
      onClose: () => live.setConnected(false),
      onHello: (unread) => queryClient.setQueryData(UNREAD.key, { count: unread }),
      onEvent,
    });
  }, [queryClient]);

  return null;
}

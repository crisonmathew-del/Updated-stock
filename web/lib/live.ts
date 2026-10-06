// The live socket (/api/ws, through the Next.js proxy on this origin): alerts for this user,
// quotes and setup events, batched by the API every 250 ms. Reconnects with backoff (1 s → 30 s)
// and pings every 25 s so idle proxies keep the connection open.

import type { LiveEvent, LiveMessage } from "@/lib/api";

export type LiveHandlers = {
  onOpen?: () => void;
  onClose?: () => void;
  onHello?: (unread: number) => void;
  onEvent: (event: LiveEvent) => void;
};

const MAX_BACKOFF_MS = 30_000;
const PING_MS = 25_000;

export function liveUrl(location: Pick<Location, "protocol" | "host">): string {
  return `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/ws`;
}

/** Connect and keep connected until the returned function is called. */
export function connectLive(handlers: LiveHandlers, url = liveUrl(window.location)): () => void {
  let socket: WebSocket | null = null;
  let stopped = false;
  let backoff = 1000;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let ping: ReturnType<typeof setInterval> | undefined;

  const open = () => {
    socket = new WebSocket(url);
    socket.onopen = () => {
      backoff = 1000;
      handlers.onOpen?.();
      ping = setInterval(() => socket?.send(JSON.stringify({ type: "ping" })), PING_MS);
    };
    socket.onmessage = (message) => {
      let parsed: LiveMessage;
      try {
        parsed = JSON.parse(String(message.data)) as LiveMessage;
      } catch {
        return;
      }
      if (parsed.type === "hello") handlers.onHello?.(parsed.unread);
      else if (parsed.type === "batch") parsed.events.forEach(handlers.onEvent);
    };
    socket.onclose = () => {
      clearInterval(ping);
      handlers.onClose?.();
      if (stopped) return;
      retry = setTimeout(open, backoff);
      backoff = Math.min(MAX_BACKOFF_MS, backoff * 2);
    };
  };
  open();

  return () => {
    stopped = true;
    clearTimeout(retry);
    clearInterval(ping);
    socket?.close();
  };
}

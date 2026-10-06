import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LiveEvent } from "@/lib/api";
import { connectLive, liveUrl } from "@/lib/live";

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  sent: string[] = [];
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
}

beforeEach(() => {
  FakeSocket.all = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("live socket", () => {
  it("uses this origin, wss on https", () => {
    expect(liveUrl({ protocol: "https:", host: "breakout.example" })).toBe(
      "wss://breakout.example/api/ws",
    );
    expect(liveUrl({ protocol: "http:", host: "localhost:3000" })).toBe(
      "ws://localhost:3000/api/ws",
    );
  });

  it("hands over hello and each batched event, pings, and reconnects with backoff", () => {
    const events: LiveEvent[] = [];
    const hello = vi.fn();
    const stop = connectLive({ onHello: hello, onEvent: (e) => events.push(e) }, "ws://x/api/ws");
    const first = FakeSocket.all[0];
    first.onopen?.();
    first.onmessage?.({ data: JSON.stringify({ type: "hello", unread: 3 }) });
    first.onmessage?.({
      data: JSON.stringify({
        type: "batch",
        events: [
          { type: "quotes", data: [{ symbol: "SPOT", last: 92.7 }] },
          { type: "setup_event", data: { kind: "breakout_provisional", symbol: "SPOT" } },
        ],
      }),
    });
    first.onmessage?.({ data: "not json" });
    expect(hello).toHaveBeenCalledWith(3);
    expect(events.map((e) => e.type)).toEqual(["quotes", "setup_event"]);
    vi.advanceTimersByTime(25_000);
    expect(first.sent).toEqual([JSON.stringify({ type: "ping" })]);

    // Dropped: back in 1 s, then 2 s.
    first.onclose?.();
    vi.advanceTimersByTime(999);
    expect(FakeSocket.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(2);
    FakeSocket.all[1].onclose?.();
    vi.advanceTimersByTime(1999);
    expect(FakeSocket.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(3);

    stop();
    expect(FakeSocket.all[2].closed).toBe(true);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.all).toHaveLength(3); // stopped: no reconnect
  });
});
